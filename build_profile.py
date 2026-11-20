"""Build-time profile. The `readonly` image target overwrites this file with READONLY = True
and makes it read-only, so no environment variable or request can turn writes back on."""
READONLY = False
