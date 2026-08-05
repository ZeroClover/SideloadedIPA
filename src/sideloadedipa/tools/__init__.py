"""Package-owned operational tools used by qualification workflows.

`qualify_backend` and its helpers are manual operator entry points: they run
outside the automated production pipeline and are invoked only through the
registered console script. They are not imported by production pipeline code.
"""
