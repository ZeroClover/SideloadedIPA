#!/usr/bin/env ruby
# frozen_string_literal: true

require 'spaceship'
require 'toml-rb'
require 'base64'
require 'fileutils'
require 'json'
require 'digest'
require 'time'
require 'set'

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
    @cache_dir = File.expand_path('../work/cache', __dir__)
    FileUtils.mkdir_p(@cache_dir)
    @cache_old_dir = File.expand_path('../work/cache-old', __dir__)
    FileUtils.mkdir_p(@cache_old_dir)
    # Check if we should skip regeneration (only fetch device list and download existing profiles)
    @skip_regeneration = ENV['SKIP_PROFILE_REGENERATION']&.downcase == 'true'
    @devices = nil
    @certificates = nil
  end

  def check_entitlements
    tasks = load_tasks
    devices = fetch_devices

    puts "[info] Found #{devices.count} iOS devices"

    # Save device list to cache for change detection
    save_device_list_cache(devices)

    devices_changed = compare_cached_device_lists

    missing = []

    tasks.each do |task|
      bundle_id = task['bundle_id']
      app_name = task['app_name']
      task_name = task['task_name']
      profile_name = "#{app_name} Dev"

      puts "=" * 80
      puts "[task] Checking profile for #{app_name} (#{bundle_id})"

      # Validate Bundle ID exists (fail fast if not)
      bundle_id_resource = find_or_fail_bundle_id(bundle_id)

      profile = find_profile(profile_name, bundle_id_resource)
      if profile
        puts "[info] Found profile: #{profile_name}"
      else
        puts "[warn] Missing profile: #{profile_name} (task: #{task_name})"
        missing << task_name
      end
    end

    all_profiles_present = missing.empty?

    write_github_outputs(
      'devices_changed' => devices_changed ? 'true' : 'false',
      'all_profiles_present' => all_profiles_present ? 'true' : 'false',
      'missing_profiles' => JSON.generate(missing)
    )

    puts '[summary] Entitlements profile check completed'
    puts "[summary] Devices changed: #{devices_changed ? 'yes' : 'no'}"
    puts "[summary] Missing profiles: #{missing.count}"
  end

  def sync_all
    tasks = load_tasks

    if @skip_regeneration
      puts '[info] SKIP_PROFILE_REGENERATION=true - downloading existing profiles only'
      tasks.each do |task|
        download_existing_profile(task)
      end
      puts '[summary] Profile sync completed'
      return
    else
      devices = fetch_devices
      @devices = devices

      puts "[info] Found #{devices.count} iOS devices"

      # Save device list to cache for change detection
      save_device_list_cache(devices)

      puts '[info] Regenerating all provisioning profiles'
      certificates = fetch_certificates
      puts "[info] Found #{certificates.count} development certificates"

      tasks.each do |task|
        sync_profile(task, certificates, devices)
      end
    end

    puts '[summary] Profile sync completed'
  end

  private
  def write_github_outputs(outputs)
    github_output = ENV['GITHUB_OUTPUT']
    return unless github_output && !github_output.empty?

    File.open(github_output, 'a') do |f|
      outputs.each do |key, value|
        f.puts "#{key}=#{value}"
      end
    end
  end

  def load_device_list_cache(path)
    return nil unless File.exist?(path)

    JSON.parse(File.read(path))
  rescue JSON::ParserError => e
    puts "[warn] Failed to parse #{path}: #{e.message}"
    nil
  end

  def compare_cached_device_lists
    cached_path = File.join(@cache_old_dir, 'device-list.json')
    current_path = File.join(@cache_dir, 'device-list.json')

    cached = load_device_list_cache(cached_path)
    current = load_device_list_cache(current_path)

    if cached.nil?
      puts '[info] No cached device list found - first run, devices considered changed'
      return true
    end

    if current.nil?
      puts '[error] No current device list found - devices considered changed'
      return true
    end

    cached_checksum = cached['checksum'] || calculate_device_checksum(cached['devices'] || [])
    current_checksum = current['checksum'] || calculate_device_checksum(current['devices'] || [])

    if cached_checksum != current_checksum
      puts '[info] Device list changed:'
      puts "  Cached checksum:  #{cached_checksum}"
      puts "  Current checksum: #{current_checksum}"

      cached_devices = (cached['devices'] || []).to_h { |d| [d['id'], d] }
      current_devices = (current['devices'] || []).to_h { |d| [d['id'], d] }

      cached_ids = cached_devices.keys.to_set
      current_ids = current_devices.keys.to_set

      added_ids = (current_ids - cached_ids).to_a.sort
      removed_ids = (cached_ids - current_ids).to_a.sort

      if added_ids.any?
        puts "  → #{added_ids.count} device(s) added:"
        added_ids.each do |device_id|
          device = current_devices[device_id]
          puts "     + #{device['name']} (#{device['device_class']})"
        end
      end

      if removed_ids.any?
        puts "  → #{removed_ids.count} device(s) removed:"
        removed_ids.each do |device_id|
          device = cached_devices[device_id]
          puts "     - #{device['name']} (#{device['device_class']})"
        end
      end

      return true
    end

    puts '[info] Device list unchanged'
    false
  end

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

  def fetch_certificates_once
    return @certificates if @certificates

    @certificates = fetch_certificates
    puts "[info] Found #{@certificates.count} development certificates"
    @certificates
  end

  def fetch_devices
    puts '[info] Fetching iOS devices...'
    # Filter iOS devices at API level
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
    existing_profile = find_profile(profile_name, bundle_id_resource)

    if existing_profile
      update_profile(existing_profile, certificates, devices)
    else
      create_profile(profile_name, bundle_id_resource, certificates, devices)
    end

    # Download profile
    download_profile(profile_name, bundle_id, task['task_name'])
  end

  def download_existing_profile(task)
    bundle_id = task['bundle_id']
    app_name = task['app_name']
    task_name = task['task_name']
    profile_name = "#{app_name} Dev"

    puts "=" * 80
    puts "[task] Downloading existing profile for #{app_name} (#{bundle_id})"

    # Find or create bundle ID (just to validate it exists)
    bundle_id_resource = find_or_fail_bundle_id(bundle_id)

    # Find existing profile
    existing_profile = find_profile(profile_name, bundle_id_resource)

    if existing_profile
      puts "[info] Found existing profile: #{profile_name}"
      download_profile(profile_name, bundle_id, task_name)
    else
      puts "[warn] No existing profile found for #{profile_name}"
      puts "[info] Creating missing profile for new task: #{task_name}"
      certificates = fetch_certificates_once
      devices = @devices || fetch_devices
      @devices ||= devices
      create_profile(profile_name, bundle_id_resource, certificates, devices)
      download_profile(profile_name, bundle_id, task_name)
    end
  end

  def find_or_fail_bundle_id(identifier)
    puts "[info] Looking up Bundle ID: #{identifier}"
    bundle_ids = Spaceship::ConnectAPI::BundleId.all(filter: { identifier: identifier })

    if bundle_ids.empty?
      raise "Bundle ID '#{identifier}' not found in App Store Connect. Please create it first."
    end

    bundle_ids.first
  end

  def find_profile(name, bundle_id_resource)
    puts "[info] Checking for existing profile: #{name}"
    profiles = Spaceship::ConnectAPI::Profile.all(
      filter: {
        profileType: PROFILE_TYPE,
        name: name
      },
      includes: 'bundleId'
    )

    return profiles.first unless bundle_id_resource

    profiles.find { |p| p.bundle_id&.id == bundle_id_resource.id }
  end

  def create_profile(name, bundle_id_resource, certificates, devices)
    puts "[info] Creating new profile: #{name}"

    # Filter devices: iOS App Development profiles can only include IPHONE and IPAD
    # APPLE_WATCH and APPLE_TV require separate provisioning profiles
    compatible_devices = devices.select { |d| %w[IPHONE IPAD].include?(d.device_class) }

    puts "[info] Using #{compatible_devices.count} compatible devices (#{devices.count - compatible_devices.count} excluded)"

    profile = Spaceship::ConnectAPI::Profile.create(
      name: name,
      profile_type: PROFILE_TYPE,
      bundle_id_id: bundle_id_resource.id,
      certificate_ids: certificates.map(&:id),
      device_ids: compatible_devices.map(&:id)
    )

    puts "[info] Profile created successfully: #{profile.id}"
    profile
  end

  def update_profile(profile, certificates, devices)
    puts "[info] Updating existing profile: #{profile.name}"

    # Note: ConnectAPI doesn't have a direct update method for profiles
    # We need to delete and recreate
    # IMPORTANT: Save bundle_id reference BEFORE deleting profile

    profile_name = profile.name
    bundle_id_resource = profile.bundle_id

    profile.delete!
    puts "[info] Deleted old profile"

    create_profile(profile_name, bundle_id_resource, certificates, devices)
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

    # Download profile content (returns base64 encoded string from API)
    profile_content = profile.profile_content

    # Decode base64 content before writing to file
    # App Store Connect API returns profileContent as base64 encoded string
    decoded_content = Base64.decode64(profile_content)

    # Save to output directory with task name for easy lookup
    output_path = File.join(@output_dir, "#{task_name}.mobileprovision")
    File.write(output_path, decoded_content)

    puts "[info] Profile downloaded to: #{output_path}"
  end

  def save_device_list_cache(devices)
    puts '[info] Saving device list cache...'

    # Convert devices to serializable format
    device_data = devices.map do |device|
      {
        'id' => device.id,
        'name' => device.name,
        'platform' => device.platform,
        'device_class' => device.device_class,
        'udid' => device.udid,
        'status' => device.status
      }
    end

    # Calculate checksum for quick comparison
    checksum = calculate_device_checksum(device_data)

    cache_data = {
      'devices' => device_data,
      'last_updated' => Time.now.utc.iso8601,
      'checksum' => checksum
    }

    cache_path = File.join(@cache_dir, 'device-list.json')
    File.write(cache_path, JSON.pretty_generate(cache_data))

    puts "[info] Device list cache saved to: #{cache_path}"
    puts "[info] Checksum: #{checksum}"
  end

  def calculate_device_checksum(device_data)
    # Sort by device ID for deterministic ordering
    normalized = device_data.sort_by { |d| d['id'] }
    json_str = JSON.generate(normalized, { space: '', object_nl: '', array_nl: '' })
    digest = Digest::SHA256.hexdigest(json_str)
    "sha256:#{digest}"
  end

end

# Main execution
if __FILE__ == $PROGRAM_NAME
  begin
    syncer = ProfileSyncer.new
    case ARGV[0]
    when 'check'
      syncer.check_entitlements
    else
      syncer.sync_all
    end
    exit 0
  rescue StandardError => e
    puts "[error] #{e.class}: #{e.message}"
    puts e.backtrace.join("\n") if ENV['DEBUG']&.downcase == 'true'
    exit 1
  end
end
