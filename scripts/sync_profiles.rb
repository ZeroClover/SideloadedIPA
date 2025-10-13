#!/usr/bin/env ruby
# frozen_string_literal: true

require 'spaceship'
require 'toml-rb'
require 'base64'
require 'fileutils'

# Sync development provisioning profiles with all devices and certificates
class ProfileSyncer
  PROFILE_TYPE = Spaceship::ConnectAPI::Profile::ProfileType::IOS_APP_DEVELOPMENT
  CERT_TYPES = [
    Spaceship::ConnectAPI::Certificate::CertificateType::DEVELOPMENT,
    Spaceship::ConnectAPI::Certificate::CertificateType::IOS_DEVELOPMENT
  ].freeze
  # Only fetch iOS devices to avoid deviceClass compatibility issues with Mac devices
  DEVICE_PLATFORM = 'IOS'.freeze

  def initialize
    @api_key_path = setup_api_key
    authenticate
    @output_dir = File.expand_path('../work/profiles', __dir__)
    FileUtils.mkdir_p(@output_dir)
  end

  def sync_all
    tasks = load_tasks
    certificates = fetch_certificates
    devices = fetch_devices

    puts "[info] Found #{certificates.count} development certificates"
    puts "[info] Found #{devices.count} iOS devices"

    tasks.each do |task|
      sync_profile(task, certificates, devices)
    end

    puts '[summary] Profile sync completed'
  end

  private

  def setup_api_key
    key_id = ENV['ASC_KEY_ID']
    issuer_id = ENV['ASC_ISSUER_ID']
    key_content = ENV['ASC_PRIVATE_KEY']

    raise 'Missing required environment variables: ASC_KEY_ID, ASC_ISSUER_ID, ASC_PRIVATE_KEY' unless key_id && issuer_id && key_content

    # Decode base64 if needed
    key_content = Base64.decode64(key_content) if key_content.match?(/^[A-Za-z0-9+\/=]+$/) && !key_content.include?('BEGIN')

    key_path = '/tmp/api_key.p8'
    File.write(key_path, key_content)

    puts "[info] API key written to #{key_path}"
    key_path
  end

  def authenticate
    Spaceship::ConnectAPI.token = Spaceship::ConnectAPI::Token.create(
      key_id: ENV['ASC_KEY_ID'],
      issuer_id: ENV['ASC_ISSUER_ID'],
      filepath: @api_key_path
    )
    puts '[info] Authenticated with App Store Connect API'
  end

  def load_tasks
    config_path = ENV['CONFIG_TOML'] || 'configs/tasks.toml'
    config = TomlRB.load_file(config_path)
    tasks = config['tasks'] || []

    raise "No tasks defined in #{config_path}" if tasks.empty?

    tasks.each do |task|
      raise "Task missing bundle_id: #{task}" unless task['bundle_id']
    end

    tasks
  end

  def fetch_certificates
    puts '[info] Fetching development certificates...'
    all_certs = Spaceship::ConnectAPI::Certificate.all

    all_certs.select do |cert|
      CERT_TYPES.include?(cert.certificate_type)
    end
  end

  def fetch_devices
    puts '[info] Fetching iOS devices...'
    # Filter iOS devices at API level to avoid fetching incompatible Mac devices
    all_devices = Spaceship::ConnectAPI::Device.all(filter: { platform: DEVICE_PLATFORM })

    all_devices.select do |device|
      device.status == 'ENABLED'
    end
  end

  def sync_profile(task, certificates, devices)
    bundle_id = task['bundle_id']
    app_name = task['app_name']
    profile_name = "#{app_name} Dev"

    puts "=" * 80
    puts "[task] Syncing profile for #{app_name} (#{bundle_id})"

    # Find or create bundle ID
    bundle_id_resource = find_or_fail_bundle_id(bundle_id)

    # Find existing profile
    existing_profile = find_profile(profile_name, bundle_id)

    if existing_profile
      update_profile(existing_profile, certificates, devices)
    else
      create_profile(profile_name, bundle_id_resource, certificates, devices)
    end

    # Download profile
    download_profile(profile_name, bundle_id, task['task_name'])
  end

  def find_or_fail_bundle_id(identifier)
    puts "[info] Looking up Bundle ID: #{identifier}"
    bundle_ids = Spaceship::ConnectAPI::BundleId.all(filter: { identifier: identifier })

    if bundle_ids.empty?
      raise "Bundle ID '#{identifier}' not found in App Store Connect. Please create it first."
    end

    bundle_ids.first
  end

  def find_profile(name, bundle_id)
    puts "[info] Checking for existing profile: #{name}"
    profiles = Spaceship::ConnectAPI::Profile.all(
      filter: {
        profileType: PROFILE_TYPE,
        name: name
      }
    )

    profiles.first
  end

  def create_profile(name, bundle_id_resource, certificates, devices)
    puts "[info] Creating new profile: #{name}"

    profile = Spaceship::ConnectAPI::Profile.create(
      name: name,
      profile_type: PROFILE_TYPE,
      bundle_id_id: bundle_id_resource.id,
      certificate_ids: certificates.map(&:id),
      device_ids: devices.map(&:id)
    )

    puts "[info] Profile created successfully: #{profile.id}"
    profile
  end

  def update_profile(profile, certificates, devices)
    puts "[info] Updating existing profile: #{profile.name}"

    # Note: Spaceship doesn't have a direct update method for profiles
    # We need to delete and recreate, or use the lower-level API
    # For simplicity, we'll delete and recreate

    profile.delete!
    puts "[info] Deleted old profile"

    # Get bundle_id from the profile
    bundle_id_resource = profile.bundle_id

    create_profile(profile.name, bundle_id_resource, certificates, devices)
  end

  def download_profile(profile_name, bundle_id, task_name)
    puts "[info] Downloading profile..."

    profiles = Spaceship::ConnectAPI::Profile.all(
      filter: {
        profileType: PROFILE_TYPE,
        name: profile_name
      }
    )

    profile = profiles.first
    raise "Profile '#{profile_name}' not found after creation" unless profile

    # Download profile content
    profile_content = profile.profile_content

    # Save to output directory with task name for easy lookup
    output_path = File.join(@output_dir, "#{task_name}.mobileprovision")
    File.write(output_path, profile_content)

    puts "[info] Profile downloaded to: #{output_path}"
  end

end

# Main execution
if __FILE__ == $PROGRAM_NAME
  begin
    syncer = ProfileSyncer.new
    syncer.sync_all
    exit 0
  rescue StandardError => e
    puts "[error] #{e.class}: #{e.message}"
    puts e.backtrace.join("\n") if ENV['DEBUG']
    exit 1
  end
end
