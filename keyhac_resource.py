
keyhac_appname = "Keyhac"
keyhac_dirname = "Keyhac"
keyhac_version = "1.00"

_startup_string_fmt = """\
%s version %s:
  http://sites.google.com/site/craftware/
"""

def startupString():
    return _startup_string_fmt % ( keyhac_appname, keyhac_version )
