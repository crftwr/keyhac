
keyhac_appname = "keyhac"
keyhac_dirname = "keyhac"
keyhac_version = "1.61"

_startup_string_fmt = """\
%s version %s:
  http://sites.google.com/site/craftware/
"""

def startupString():
    return _startup_string_fmt % ( keyhac_appname, keyhac_version )
