"""Test package.

Present so test modules can share helpers via relative imports (``from
.fixtures import ...``) without putting a top-level ``fixtures`` module on
``sys.path``, where it could shadow or be shadowed by a real dependency.
"""
