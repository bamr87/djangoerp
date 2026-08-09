"""Django applications.

Every app in this package is registered in INSTALLED_APPS as ``apps.<name>``;
its Django *label* stays the bare name (``journal``, ``coa``, ...), which is
what migrations, ``ForeignKey('coa.Account')`` strings and the admin all key
off. Renaming a directory here therefore changes import paths but not the
database — pin ``AppConfig.label`` if you ever need to break that.
"""
