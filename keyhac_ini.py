import os
import sys
import configparser

import ckit

if ckit.platform()=="win":
    import msvcrt

import keyhac_resource


## @addtogroup ini
## @{

ini = None
ini_filename = os.path.join( ckit.getAppExePath(), 'keyhac.ini' )

#--------------------------------------------------------------------

def read():

    global ini

    ini = configparser.RawConfigParser()

    try:
        fd = open( ini_filename, "r" )
        if ckit.platform()=="win":
            svcrt.locking( fd.fileno(), msvcrt.LK_LOCK, 1 )
        ini.readfp(fd)
        fd.close()
    except Exception as e:
        pass

    #--------------------------------------------
        
    try:
        ini.add_section("GLOBAL")
    except configparser.DuplicateSectionError:
        pass

    try:
        ini.add_section("CONSOLE")
    except configparser.DuplicateSectionError:
        pass

    try:
        ini.add_section("CLIPBOARD")
    except configparser.DuplicateSectionError:
        pass

    try:
        ini.add_section("DEBUG")
    except configparser.DuplicateSectionError:
        pass

    #--------------------------------------------

    ini.set( "GLOBAL", "version", keyhac_resource.keyhac_version )


def write():
    try:
        fd = open( ini_filename, "w" )
        if ckit.platform()=="win":
            svcrt.locking( fd.fileno(), msvcrt.LK_LOCK, 1 )
        ini.write(fd)
        fd.close()
    except:
        pass


## INI ファイルの文字列アイテムを取得する
def get( section, option, default=None ):
    #print( "ini.get", section, option )
    try:
        return ini.get( section, option )
    except:
        if default!=None:
            return default
        raise

## INI ファイルの整数アイテムを取得する
def getint( section, option, default=None ):
    #print( "ini.getint", section, option )
    try:
        return ini.getint( section, option )
    except:
        if default!=None:
            return default
        raise

## INI ファイルに文字列のアイテムを設定する
def set( section, option, value ):
    #print( "ini.set", section, option, value )
    ini.set( section, option, value )

## INI ファイルに整数のアイテムを設定する
def setint( section, option, value ):
    #print( "ini.setint", section, option, value )
    assert( type(value)==int )
    ini.set( section, option, str(value) )

## INI ファイルのセクションを削除する
def remove_section(section):
    result = ini.remove_section(section)
    return result

## INI ファイルの設定アイテムを削除する
def remove_option( section, option ):
    result = ini.remove_option( section, option )
    return result

## @} ini

