import os
import sys
import time
import subprocess
import profile
import ctypes
import fnmatch
import traceback

import ckit
from ckit.ckit_const import *

if ckit.platform()=="win":
    import winsound

if ckit.platform()=="mac":
    import accessibility

import keyhac_hook
import keyhac_clipboard
import keyhac_listwindow
import keyhac_balloon
import keyhac_ini
import keyhac_resource
import keyhac_misc


## @addtogroup keymap
## @{

#--------------------------------------------------------------------

MODKEY_ALT   = 0x00000001
MODKEY_CTRL  = 0x00000002
MODKEY_SHIFT = 0x00000004
MODKEY_WIN   = 0x00000008
MODKEY_CMD   = 0x00000010
MODKEY_FN    = 0x00000020
MODKEY_USER0 = 0x00000040
MODKEY_USER1 = 0x00000080

MODKEY_ALT_L   = 0x00000100
MODKEY_CTRL_L  = 0x00000200
MODKEY_SHIFT_L = 0x00000400
MODKEY_WIN_L   = 0x00000800
MODKEY_CMD_L   = 0x00001000
MODKEY_FN_L    = 0x00002000
MODKEY_USER0_L = 0x00004000
MODKEY_USER1_L = 0x00008000

MODKEY_ALT_R   = 0x00010000
MODKEY_CTRL_R  = 0x00020000
MODKEY_SHIFT_R = 0x00040000
MODKEY_WIN_R   = 0x00080000
MODKEY_CMD_R   = 0x00100000
MODKEY_FN_R    = 0x00200000
MODKEY_USER0_R = 0x00400000
MODKEY_USER1_R = 0x00800000

MODKEY_USER_ALL = ( MODKEY_USER0   | MODKEY_USER1
                  | MODKEY_USER0_L | MODKEY_USER1_L
                  | MODKEY_USER0_R | MODKEY_USER1_R )


def checkModifier( mod1, mod2 ):

    _mod1 = mod1 & 0xff
    _mod2 = mod2 & 0xff
    _mod1_L = (mod1 & 0xff00) >> 8
    _mod2_L = (mod2 & 0xff00) >> 8
    _mod1_R = (mod1 & 0xff0000) >> 16
    _mod2_R = (mod2 & 0xff0000) >> 16

    if _mod1 & ~( _mod2 | _mod2_L | _mod2_R ):
        return False
    if _mod1_L & ~( _mod2 | _mod2_L ):
        return False
    if _mod1_R & ~( _mod2 | _mod2_R ):
        return False
    if _mod2 & ~( _mod1 | _mod1_L | _mod1_R ):
        return False
    if _mod2_L & ~( _mod1 | _mod1_L ):
        return False
    if _mod2_R & ~( _mod1 | _mod1_R ):
        return False
    return True


class KeyCondition:

    vk_str_table = {}
    str_vk_table = {}

    vk_str_table_common = {

        VK_A : "A",
        VK_B : "B",
        VK_C : "C",
        VK_D : "D",
        VK_E : "E",
        VK_F : "F",
        VK_G : "G",
        VK_H : "H",
        VK_I : "I",
        VK_J : "J",
        VK_K : "K",
        VK_L : "L",
        VK_M : "M",
        VK_N : "N",
        VK_O : "O",
        VK_P : "P",
        VK_Q : "Q",
        VK_R : "R",
        VK_S : "S",
        VK_T : "T",
        VK_U : "U",
        VK_V : "V",
        VK_W : "W",
        VK_X : "X",
        VK_Y : "Y",
        VK_Z : "Z",

        VK_0 : "0",
        VK_1 : "1",
        VK_2 : "2",
        VK_3 : "3",
        VK_4 : "4",
        VK_5 : "5",
        VK_6 : "6",
        VK_7 : "7",
        VK_8 : "8",
        VK_9 : "9",

        VK_MINUS  : "Minus",
        VK_PLUS   : "Plus",
        VK_COMMA  : "Comma",
        VK_PERIOD : "Period",

        #VK_NUMLOCK  : "NumLock", # FIXME : Mac対応
        VK_DIVIDE   : "Divide",
        VK_MULTIPLY : "Multiply",
        VK_SUBTRACT : "Subtract",
        VK_ADD      : "Add",
        VK_DECIMAL  : "Decimal",

        VK_NUMPAD0 : "Num0",
        VK_NUMPAD1 : "Num1",
        VK_NUMPAD2 : "Num2",
        VK_NUMPAD3 : "Num3",
        VK_NUMPAD4 : "Num4",
        VK_NUMPAD5 : "Num5",
        VK_NUMPAD6 : "Num6",
        VK_NUMPAD7 : "Num7",
        VK_NUMPAD8 : "Num8",
        VK_NUMPAD9 : "Num9",

        VK_F1  : "F1",
        VK_F2  : "F2",
        VK_F3  : "F3",
        VK_F4  : "F4",
        VK_F5  : "F5",
        VK_F6  : "F6",
        VK_F7  : "F7",
        VK_F8  : "F8",
        VK_F9  : "F9",
        VK_F10 : "F10",
        VK_F11 : "F11",
        VK_F12 : "F12",

        VK_LEFT     : "Left",
        VK_RIGHT    : "Right",
        VK_UP       : "Up",
        VK_DOWN     : "Down",
        VK_SPACE    : "Space",
        VK_TAB      : "Tab",
        VK_BACK     : "Back",
        VK_RETURN   : "Return",
        VK_ESCAPE   : "Escape",
        VK_CAPITAL  : "CapsLock",
        #VK_APPS     : "Apps", # FIXME : Mac対応

        #VK_INSERT   : "Insert", # FIXME : Mac対応
        VK_DELETE   : "Delete",
        VK_HOME     : "Home",
        VK_END      : "End",
        VK_NEXT     : "PageDown",
        VK_PRIOR    : "PageUp",

        #VK_MENU     : "Alt", # FIXME : Mac対応
        VK_LMENU    : "LAlt",
        VK_RMENU    : "RAlt",
        #VK_CONTROL  : "Ctrl", # FIXME : Mac対応
        VK_LCONTROL : "LCtrl",
        VK_RCONTROL : "RCtrl",
        #VK_SHIFT    : "Shift", # FIXME : Mac対応
        VK_LSHIFT   : "LShift",
        VK_RSHIFT   : "RShift",
        #VK_LWIN     : "LWin", # FIXME : Mac対応
        #VK_RWIN     : "RWin", # FIXME : Mac対応
        VK_LCOMMAND : "LCmd",
        VK_RCOMMAND : "RCmd",
        VK_FUNCTION : "Fn",

        #VK_SNAPSHOT : "PrintScreen", # FIXME : Mac対応
        #VK_SCROLL   : "ScrollLock", # FIXME : Mac対応
        #VK_PAUSE    : "Pause", # FIXME : Mac対応
    }

    if ckit_misc.platform()=="win":

        vk_str_table_std = {
            VK_OEM_1 : "Semicolon",
            VK_OEM_2 : "Slash",
            VK_OEM_3 : "BackQuote",
            VK_OEM_4 : "OpenBracket",
            VK_OEM_5 : "BackSlash",
            VK_OEM_6 : "CloseBracket",
            VK_OEM_7 : "Quote",
        }

        vk_str_table_jpn = {
            VK_OEM_1    : "Colon",
            VK_OEM_2    : "Slash",
            VK_OEM_3    : "Atmark",
            VK_OEM_4    : "OpenBracket",
            VK_OEM_5    : "Yen",
            VK_OEM_6    : "CloseBracket",
            VK_OEM_7    : "Caret",
            VK_OEM_102  : "BackSlash",
        }

    else:

        vk_str_table_std = {
            VK_SEMICOLON    : "Semicolon",
            VK_SLASH        : "Slash",
            VK_GRAVE        : "BackQuote",
            VK_OPENBRACKET  : "OpenBracket",
            VK_BACKSLASH    : "BackSlash",
            VK_CLOSEBRACKET : "CloseBracket",
            VK_QUOTE        : "Quote",
        }

        vk_str_table_jpn = {
            #VK_OEM_1        : "Colon", # FIXME : Mac対応
            VK_SLASH        : "Slash",
            #VK_OEM_3        : "Atmark", # FIXME : Mac対応
            VK_OPENBRACKET  : "OpenBracket",
            #VK_OEM_5        : "Yen", # FIXME : Mac対応
            VK_CLOSEBRACKET : "CloseBracket",
            #VK_OEM_7        : "Caret", # FIXME : Mac対応
            VK_BACKSLASH    : "BackSlash",
        }

    str_vk_table_common = {

        "A" : VK_A,
        "B" : VK_B,
        "C" : VK_C,
        "D" : VK_D,
        "E" : VK_E,
        "F" : VK_F,
        "G" : VK_G,
        "H" : VK_H,
        "I" : VK_I,
        "J" : VK_J,
        "K" : VK_K,
        "L" : VK_L,
        "M" : VK_M,
        "N" : VK_N,
        "O" : VK_O,
        "P" : VK_P,
        "Q" : VK_Q,
        "R" : VK_R,
        "S" : VK_S,
        "T" : VK_T,
        "U" : VK_U,
        "V" : VK_V,
        "W" : VK_W,
        "X" : VK_X,
        "Y" : VK_Y,
        "Z" : VK_Z,

        "0" : VK_0,
        "1" : VK_1,
        "2" : VK_2,
        "3" : VK_3,
        "4" : VK_4,
        "5" : VK_5,
        "6" : VK_6,
        "7" : VK_7,
        "8" : VK_8,
        "9" : VK_9,

        "MINUS"  : VK_MINUS,
        "PLUS"   : VK_PLUS,
        "COMMA"  : VK_COMMA,
        "PERIOD" : VK_PERIOD,

        #"NUMLOCK"  : VK_NUMLOCK, # FIXME : Mac対応
        "DIVIDE"   : VK_DIVIDE,
        "MULTIPLY" : VK_MULTIPLY,
        "SUBTRACT" : VK_SUBTRACT,
        "ADD"      : VK_ADD,
        "DECIMAL"  : VK_DECIMAL,

        "NUM0" : VK_NUMPAD0,
        "NUM1" : VK_NUMPAD1,
        "NUM2" : VK_NUMPAD2,
        "NUM3" : VK_NUMPAD3,
        "NUM4" : VK_NUMPAD4,
        "NUM5" : VK_NUMPAD5,
        "NUM6" : VK_NUMPAD6,
        "NUM7" : VK_NUMPAD7,
        "NUM8" : VK_NUMPAD8,
        "NUM9" : VK_NUMPAD9,

        "F1"  : VK_F1,
        "F2"  : VK_F2,
        "F3"  : VK_F3,
        "F4"  : VK_F4,
        "F5"  : VK_F5,
        "F6"  : VK_F6,
        "F7"  : VK_F7,
        "F8"  : VK_F8,
        "F9"  : VK_F9,
        "F10" : VK_F10,
        "F11" : VK_F11,
        "F12" : VK_F12,

        "LEFT"     : VK_LEFT  ,
        "RIGHT"    : VK_RIGHT ,
        "UP"       : VK_UP    ,
        "DOWN"     : VK_DOWN  ,
        "SPACE"    : VK_SPACE ,
        "TAB"      : VK_TAB   ,
        "BACK"     : VK_BACK  ,
        "RETURN"   : VK_RETURN,
        "ENTER"    : VK_RETURN,
        "ESCAPE"   : VK_ESCAPE,
        "ESC"      : VK_ESCAPE,
        "CAPSLOCK" : VK_CAPITAL,
        "CAPS"     : VK_CAPITAL,
        "CAPITAL"  : VK_CAPITAL,
        #"APPS"     : VK_APPS, # FIXME : Mac対応

        #"INSERT"   : VK_INSERT, # FIXME : Mac対応
        "DELETE"   : VK_DELETE,
        "HOME"     : VK_HOME,
        "END"      : VK_END,
        "PAGEDOWN" : VK_NEXT,
        "PAGEUP"   : VK_PRIOR,

        "ALT"  : VK_LMENU,
        "LALT" : VK_LMENU,
        "RALT" : VK_RMENU,
        "CTRL"  : VK_LCONTROL,
        "LCTRL" : VK_LCONTROL,
        "RCTRL" : VK_RCONTROL,
        "SHIFT"  : VK_LSHIFT,
        "LSHIFT" : VK_LSHIFT,
        "RSHIFT" : VK_RSHIFT,
        #"LWIN" : VK_LWIN, # FIXME : Mac対応
        #"RWIN" : VK_RWIN, # FIXME : Mac対応
        "CMD"  : VK_LCOMMAND,
        "LCMD" : VK_LCOMMAND,
        "RCMD" : VK_RCOMMAND,
        "FN" : VK_FUNCTION,

        #"PRINTSCREEN" : VK_SNAPSHOT, # FIXME : Mac対応
        #"SCROLLLOCK"  : VK_SCROLL, # FIXME : Mac対応
        #"PAUSE"       : VK_PAUSE, # FIXME : Mac対応
    }

    if ckit_misc.platform()=="win":

        str_vk_table_std = {

            "SEMICOLON"     : VK_OEM_1,
            "COLON"         : VK_OEM_1,
            "SLASH"         : VK_OEM_2,
            "BACKQUOTE"     : VK_OEM_3,
            "TILDE"         : VK_OEM_3,
            "OPENBRACKET"   : VK_OEM_4,
            "BACKSLASH"     : VK_OEM_5,
            "YEN"           : VK_OEM_5,
            "CLOSEBRACKET"  : VK_OEM_6,
            "QUOTE"         : VK_OEM_7,
            "DOUBLEQUOTE"   : VK_OEM_7,
            "UNDERSCORE"    : VK_OEM_MINUS,
            "ASTERISK"      : VK_8,
            "ATMARK"        : VK_2,
            "CARET"         : VK_6,
        }

        str_vk_table_jpn = {

            "SEMICOLON"     : VK_OEM_PLUS,
            "COLON"         : VK_OEM_1,
            "SLASH"         : VK_OEM_2,
            "BACKQUOTE"     : VK_OEM_3,
            "TILDE"         : VK_OEM_7,
            "OPENBRACKET"   : VK_OEM_4,
            "BACKSLASH"     : VK_OEM_102,
            "YEN"           : VK_OEM_5,
            "CLOSEBRACKET"  : VK_OEM_6,
            "QUOTE"         : VK_7,
            "DOUBLEQUOTE"   : VK_2,
            "UNDERSCORE"    : VK_OEM_102,
            "ASTERISK"      : VK_OEM_1,
            "ATMARK"        : VK_OEM_3,
            "CARET"         : VK_OEM_7,
        }

    else:

        str_vk_table_std = {

            "SEMICOLON"     : VK_SEMICOLON,
            "COLON"         : VK_SEMICOLON,
            "SLASH"         : VK_SLASH,
            "BACKQUOTE"     : VK_GRAVE,
            #"TILDE"         : VK_OEM_3, # FIXME : Mac対応
            "OPENBRACKET"   : VK_OPENBRACKET,
            "BACKSLASH"     : VK_BACKSLASH,
            "YEN"           : VK_BACKSLASH,
            "CLOSEBRACKET"  : VK_CLOSEBRACKET,
            "QUOTE"         : VK_QUOTE,
            "DOUBLEQUOTE"   : VK_QUOTE,
            "UNDERSCORE"    : VK_MINUS,
            "ASTERISK"      : VK_8,
            "ATMARK"        : VK_2,
            "CARET"         : VK_6,
        }

        str_vk_table_jpn = {

            "SEMICOLON"     : VK_PLUS,
            #"COLON"         : VK_OEM_1, # FIXME : Mac対応
            "SLASH"         : VK_SLASH,
            "BACKQUOTE"     : VK_GRAVE,
            #"TILDE"         : VK_OEM_7, # FIXME : Mac対応
            "OPENBRACKET"   : VK_OPENBRACKET,
            "BACKSLASH"     : VK_BACKSLASH,
            "YEN"           : VK_YEN,
            "CLOSEBRACKET"  : VK_CLOSEBRACKET,
            "QUOTE"         : VK_7,
            "DOUBLEQUOTE"   : VK_2,
            "UNDERSCORE"    : VK_UNDERSCORE,
            #"ASTERISK"      : VK_OEM_1, # FIXME : Mac対応
            #"ATMARK"        : VK_OEM_3, # FIXME : Mac対応
            #"CARET"         : VK_OEM_7, # FIXME : Mac対応
        }

    str_mod_table = {

        "ALT"   :  MODKEY_ALT,
        "CTRL"  :  MODKEY_CTRL,
        "SHIFT" :  MODKEY_SHIFT,
        "WIN"   :  MODKEY_WIN,
        "CMD"   :  MODKEY_CMD,
        "FN"    :  MODKEY_FN,
        "USER0" :  MODKEY_USER0,
        "USER1" :  MODKEY_USER1,

        "LALT"   :  MODKEY_ALT_L,
        "LCTRL"  :  MODKEY_CTRL_L,
        "LSHIFT" :  MODKEY_SHIFT_L,
        "LWIN"   :  MODKEY_WIN_L,
        "LCMD"   :  MODKEY_CMD_L,
        "LUSER0" :  MODKEY_USER0_L,
        "LUSER1" :  MODKEY_USER1_L,

        "RALT"   :  MODKEY_ALT_R,
        "RCTRL"  :  MODKEY_CTRL_R,
        "RSHIFT" :  MODKEY_SHIFT_R,
        "RWIN"   :  MODKEY_WIN_R,
        "RCMD"   :  MODKEY_CMD_R,
        "RUSER0" :  MODKEY_USER0_R,
        "RUSER1" :  MODKEY_USER1_R,

        #"A" :  MODKEY_ALT,
        #"C" :  MODKEY_CTRL,
        #"S" :  MODKEY_SHIFT,
        #"W" :  MODKEY_WIN,
        #"U0" : MODKEY_USER0,
        #"U1" : MODKEY_USER1,

        #"LA" :  MODKEY_ALT_L,
        #"LC" :  MODKEY_CTRL_L,
        #"LS" :  MODKEY_SHIFT_L,
        #"LW" :  MODKEY_WIN_L,
        #"LU0" : MODKEY_USER0_L,
        #"LU1" : MODKEY_USER1_L,

        #"RA" :  MODKEY_ALT_R,
        #"RC" :  MODKEY_CTRL_R,
        #"RS" :  MODKEY_SHIFT_R,
        #"RW" :  MODKEY_WIN_R,
        #"RU0" : MODKEY_USER0_R,
        #"RU1" : MODKEY_USER1_R,
    }

    def __init__( self, vk, mod=0, up=False, oneshot=False ):
        if type(vk)==str and len(vk)==1 : vk=ord(vk)
        self.vk = vk
        self.mod = mod
        self.up = up
        self.oneshot = oneshot

    def __hash__(self):
        return self.vk

    def __eq__(self,other):
        if self.vk!=other.vk : return False
        if not checkModifier( self.mod, other.mod ) : return False
        if self.up!=other.up : return False
        if self.oneshot!=other.oneshot : return False
        return True

    def __str__(self):

        s = ""

        if self.oneshot:
            s += "O-"
        elif self.up:
            s += "U-"
        else:
            s += "D-"

        if self.mod & MODKEY_ALT : s += "Alt-"
        elif self.mod & MODKEY_ALT_L : s += "LAlt-"
        elif self.mod & MODKEY_ALT_R : s += "RAlt-"

        if self.mod & MODKEY_CTRL : s += "Ctrl-"
        elif self.mod & MODKEY_CTRL_L : s += "LCtrl-"
        elif self.mod & MODKEY_CTRL_R : s += "RCtrl-"

        if self.mod & MODKEY_SHIFT : s += "Shift-"
        elif self.mod & MODKEY_SHIFT_L : s += "LShift-"
        elif self.mod & MODKEY_SHIFT_R : s += "RShift-"

        if self.mod & MODKEY_WIN : s += "Win-"
        elif self.mod & MODKEY_WIN_L : s += "LWin-"
        elif self.mod & MODKEY_WIN_R : s += "RWin-"

        if self.mod & MODKEY_CMD : s += "Cmd-"
        elif self.mod & MODKEY_CMD_L : s += "LCmd-"
        elif self.mod & MODKEY_CMD_R : s += "RCmd-"

        if self.mod & MODKEY_FN : s += "Fn-"
        elif self.mod & MODKEY_FN_L : s += "LFn-"
        elif self.mod & MODKEY_FN_R : s += "RFn-"

        if self.mod & MODKEY_USER0 : s += "User0-"
        elif self.mod & MODKEY_USER0_L : s += "LUser0-"
        elif self.mod & MODKEY_USER0_R : s += "RUser0-"

        if self.mod & MODKEY_USER1 : s += "User1-"
        elif self.mod & MODKEY_USER1_L : s += "LUser1-"
        elif self.mod & MODKEY_USER1_R : s += "RUser1-"

        s += KeyCondition.vkToStr(self.vk)

        return s

    @staticmethod
    def fromString(s):

        s = s.upper()

        vk = None
        mod=0
        up=False
        oneshot=False

        token_list = s.split("-")

        for token in token_list[:-1]:

            token = token.strip()

            try:
                mod |= KeyCondition.strToMod(token)
            except ValueError:
                if token=="O":
                    oneshot = True
                elif token=="D":
                    up = False
                elif token=="U":
                    up = True
                else:
                    raise ValueError

        token = token_list[-1].strip()

        vk = KeyCondition.strToVk(token)

        return KeyCondition( vk, mod, up=up, oneshot=oneshot )

    @staticmethod
    def initTables():

        if ckit.platform()=="win":
            keyboard_type = ctypes.windll.user32.GetKeyboardType(0)
        else:
            keyboard_type = 0

        KeyCondition.str_vk_table = KeyCondition.str_vk_table_common
        KeyCondition.vk_str_table = KeyCondition.vk_str_table_common

        if keyboard_type==7:
            KeyCondition.str_vk_table.update(KeyCondition.str_vk_table_jpn)
            KeyCondition.vk_str_table.update(KeyCondition.vk_str_table_jpn)
        else:
            KeyCondition.str_vk_table.update(KeyCondition.str_vk_table_std)
            KeyCondition.vk_str_table.update(KeyCondition.vk_str_table_std)

    @staticmethod
    def strToVk(name):
        try:
            vk = KeyCondition.str_vk_table[name.upper()]
        except KeyError:
            try:
                vk = int(name.strip("()"))
            except:
                raise ValueError
        return vk

    @staticmethod
    def vkToStr(vk):
        try:
            name = KeyCondition.vk_str_table[vk]
        except KeyError:
            name = "(%d)" % vk
        return name

    @staticmethod
    def strToMod( name, force_LR=False ):
        try:
            mod = KeyCondition.str_mod_table[ name.upper() ]
        except KeyError:
            raise ValueError
        if force_LR and (mod & 0xff):
            mod <<= 8
        return mod


class WindowKeymap:

    def __init__( self, app_name=None, check_func=None, help_string=None ):
        self.app_name = app_name
        self.check_func = check_func
        self.help_string = help_string
        self.keymap = {}

    def check( self, focus ):
        if self.app_name and ( not focus or not fnmatch.fnmatch( ckit.getApplicationNameByPid(focus.pid), self.app_name ) ) : return False
        try:
            if self.check_func and ( not focus or not self.check_func(focus) ) : return False
        except Exception as e:
            print(e)
            traceback.print_exc()
            return False
        return True

    def helpString(self):
        return self.help_string

    def __setitem__( self, name, value ):

        try:
            key_cond = KeyCondition.fromString(name)
        except ValueError:
            print( ckit.strings["error_invalid_key_expression"], name )
            return

        self.keymap[key_cond] = value

    def __getitem__( self, name ):

        try:
            key_cond = KeyCondition.fromString(name)
        except ValueError:
            print( ckit.strings["error_invalid_key_expression"], name )
            return

        return self.keymap[key_cond]

    def __delitem__( self, name ):
        try:
            key_cond = KeyCondition.fromString(name)
        except ValueError:
            print( ckit.strings["error_invalid_key_expression"], name )
            return

        del self.keymap[key_cond]


## キーの置き換えや任意の処理の実行を行うクラス
#
#  keyhacの主な機能を実現しているクラスです。\n\n
#
#  設定ファイル config.py の configure に渡される keyhac 引数は、Keymap クラスのオブジェクトです。
#
class Keymap(ckit.TextWindow):

    def __init__( self, config_filename, debug, profile ):

        ckit.TextWindow.__init__(
            self,
            x = 0,
            y = 0,
            width = 40,
            height = 10,
            font_name = "Osaka-Mono",
            font_size = 16,
            title_bar = True,
            title = "keyhac keymap",
            show = False,
            endsession_handler = self._onEndSession,
            )

        self.config_filename = config_filename  # config.py のファイルパス
        self.editor = ""                        # config.pyを編集するためのテキストエディタ
        self.quote_mark = ""                    # クリップボードから貼り付けるときの引用記号
        self.cblisters = []                     # クリップボードの履歴リストを表示するときのメニュー生成関数リスト
        self.debug = False                      # デバッグモード
        self.profile = profile                  # プロファイルモード
        self.window_keymap_list = []            # WindowKeymapオブジェクトのリスト
        self.multi_stroke_keymap = None         # マルチストローク用のWindowKeymapオブジェクト
        self.current_map = {}                   # 現在フォーカスされているウインドウで有効なキーマップ
        self.vk_mod_map = {}                    # モディファイアキーの仮想キーコードとビットのテーブル
        self.vk_vk_map = {}                     # キーの置き換えテーブル
        self.focus = None                       # 現在フォーカスされているUI要素
        self.focus_change_count = None          # フォーカス変更検出用のカウント
        self.modifier = 0                       # 押されているモディファイアキーのビットの組み合わせ
        self.last_keydown = None                # 最後にKeyDownされた仮想キーコード
        self.oneshot_canceled = False           # ワンショットモディファイアをキャンセルするか
        self.input_seq = []                     # 仮想のキー入力シーケンス ( beginInput ～ endInput で使用 )
        self.virtual_modifier = 0               # 仮想のモディファイアキー状態 ( beginInput ～ endInput で使用 )
        self.record_status = None               # キーボードマクロの状態
        self.record_seq = None                  # キーボードマクロのシーケンス
        self.hook_call_list = []                # フック内呼び出し関数のリスト

        self.console_window = None
        self.list_window = None

        self.sanity_check_state = None
        self.sanity_check_count = 0

        # TRU のときも Input.send() を呼ぶかどうかのデバッグ用フラグ。
        # クリップボード履歴リストの表示に DelayedCall を使うようにしたら
        # モディファイアキーが押しっぱなしになる現象が起きなくなったので、
        # 廃止できそう。
        # これを有効にすると、Win8 でAlt-TAB などが効かなくなる。
        self.send_input_on_tru = keyhac_ini.getint( "DEBUG", "send_input_on_tru", 0 )


        ## サブスレッドからメインスレッド中の処理を呼び出すためのSyncCallオブジェクト
        self.synccall = ckit.SyncCall()

        self.setTimer( self._onTimer, 10 )
        self.setTimer( self._onTimerCheckSanity, 100 )

        self.clipboard_history = keyhac_clipboard.cblister_ClipboardHistory( self, debug=debug )

        self.balloon = None
        self.balloon_name = ""
        self.balloon_timer = None

        self.enableHook(True)
        self.enableDebug(debug)

    def destroy(self):

        if self.balloon:
            self.balloon.destroy()
            self.balloon = None

        self.clipboard_history.destroy()
        self._releaseModifierAll()
        self.enableHook(False)

        ckit.TextWindow.destroy(self)

    def setConsoleWindow( self, console_window ):
        self.console_window = console_window

    def _onEndSession(self):
        self.clipboard_history.save()
        keyhac_ini.write()

    def _onTimer(self):
        ckit.JobQueue.checkAll()
        self.synccall.check()
        if ckit.platform()=="mac":
            self.clipboard_history.checkChanged()

    def _onTimerCheckSanity(self):
        self.checkSanity()
        self.clipboard_history.checkSanity()

    def enableHook( self, enable ):
        self.hook_enabled = enable
        if self.hook_enabled:
            keyhac_hook.hook.keydown = self._hook_onKeyDown
            keyhac_hook.hook.keyup = self._hook_onKeyUp
            if ckit.platform()=="win":
                keyhac_hook.hook.mousedown = self._hook_onMouseDown
                keyhac_hook.hook.mouseup = self._hook_onMouseUp
            keyhac_hook.hook.reset()
        else:
            keyhac_hook.hook.keydown = None
            keyhac_hook.hook.keyup = None
            if ckit.platform()=="win":
                keyhac_hook.hook.mousedown = None
                keyhac_hook.hook.mouseup = None
            keyhac_hook.hook.reset()

    def enableDebug( self, enable ):
        self.debug = enable
        self.clipboard_history.enableDebug(enable)
        if ckit.platform()=="win":
            pyauto.setDebug(enable)

    ## フォントを設定する
    #
    #  @param self  -
    #  @param name  フォント名
    #  @param size  フォントサイズ
    #
    def setFont( self, name, size ):
        ckit.TextWindow.setFont( self, name, size )
        self.console_window.setFont( name, size )

    ## テーマを設定する
    #
    #  @param self  -
    #  @param name  テーマ名
    #
    #  引数 name には、theme ディレクトリ以下のディレクトリ名を与えます。
    #
    def setTheme( self, name ):
        ckit.setTheme( name, {} )
        self.console_window.reloadTheme()

    def _releaseModifierAll(self):
        input_seq = []
        for vk_mod in self.vk_mod_map.items():
            if vk_mod[1] & MODKEY_USER_ALL:
                continue
            input_seq.append( ckit.KeyUp(vk_mod[0]) )
        ckit.Input.send(input_seq)

    ## 設定を読み込む
    #
    #  キーマップやモディファイアキーの定義などをリセットした上で、config.py を再読み込みします。
    #
    def configure(self):

        self._releaseModifierAll()

        ckit.Keymap.init()

        KeyCondition.initTables()

        self.window_keymap_list = []
        self.current_map = {}
        self.vk_mod_map = {}
        self.vk_vk_map = {}
        self.modifier = 0

        self.vk_mod_map[VK_LSHIFT   ] = MODKEY_SHIFT_L
        self.vk_mod_map[VK_RSHIFT   ] = MODKEY_SHIFT_R
        self.vk_mod_map[VK_LCONTROL ] = MODKEY_CTRL_L
        self.vk_mod_map[VK_RCONTROL ] = MODKEY_CTRL_R
        self.vk_mod_map[VK_LMENU    ] = MODKEY_ALT_L
        self.vk_mod_map[VK_RMENU    ] = MODKEY_ALT_R
        #self.vk_mod_map[VK_LWIN     ] = MODKEY_WIN_L
        #self.vk_mod_map[VK_RWIN     ] = MODKEY_WIN_R
        self.vk_mod_map[VK_LCOMMAND ] = MODKEY_CMD_L
        self.vk_mod_map[VK_RCOMMAND ] = MODKEY_CMD_R
        self.vk_mod_map[VK_FUNCTION ] = MODKEY_FN_L

        if ckit.platform()=="win":
            self.editor = "notepad.exe"
        elif ckit.platform()=="mac":
            self.editor = "TextEdit"

        self.quote_mark = "> "
        self.cblisters = [
            ( ckit.strings["title_clipboard"], self.clipboard_history )
            ]

        ckit.CronTable.defaultCronTable().cancel()
        ckit.CronTable.defaultCronTable().clear()

        ckit.reloadConfigScript( self.config_filename )
        ckit.callConfigFunc("configure",self)

        self._updateFocusWindow()

    ## テキストファイルを編集する
    #
    #  @param self  -
    #  @param filename  ファイル名
    #
    def editTextFile( self, filename ):
        if callable(self.editor):
            self.editor(filename)
        else:
            if ckit.platform()=="win":
                pyauto.shellExecute( None, self.editor, '"%s"' % filename, "" )
            elif ckit.platform()=="mac":
                subprocess.call([ "open", "-a", self.editor, filename ])

    ## config.py を編集する
    #
    #  @param self  -
    #
    def editConfigFile(self):
        self.editTextFile(self.config_filename)

    def startup(self):
        self.clipboard_history.load()
        print( keyhac_resource.startupString() )

    def _hasKeyAction( self, key ):
        return key in self.current_map

    def _keyAction( self, key ):

        if self.debug : print( "IN  :", key )

        try:
            try:
                handler = self.current_map[key]
            except KeyError:
                if self.multi_stroke_keymap and not key.up and not key.oneshot and not key.vk in self.vk_mod_map:
                    winsound.MessageBeep()
                    return True
                else:
                    return False
        finally:
            if not key.up and not key.oneshot and not key.vk in self.vk_mod_map:
                self.leaveMultiStroke()

        if callable(handler):

            self._cancelOneshotWinAlt()

            handler()

        elif isinstance(handler,WindowKeymap):

            self._cancelOneshotWinAlt()

            self.enterMultiStroke(handler)

        else:
            if type(handler)!=list and type(handler)!=tuple:
                handler = [handler]

            self.beginInput()

            for item in handler:

                if type(item)==str:
                    self.setInput_FromString(item)
                else:
                    raise TypeError;

            self.endInput()

        return True

    def _debugKeyState( self, vk ):

        state = ckit.Input.getKeyboardState()
        print( "getKeyboardState(%d): 0x%x" % ( vk, ord(state[vk]) ) )

        state = ckit.Input.getKeyState(vk)
        print( "getKeyState(%d):      0x%x" % ( vk, state ) )

        state = ckit.Input.getAsyncKeyState(vk)
        print( "getAsyncKeyState(%d): 0x%x" % ( vk, state ) )

    def _recordKey( self, vk, up ):
        if self.record_status=="recording":
            if len(self.record_seq)>=1000:
                print( ckit.strings["error_macro_too_long"] )
                return
            self.record_seq.append( ( vk, up ) )

    def _onKeyDown( self, vk ):

        if ckit.platform()=="win":
            # FIXME : vk=0 は Macでは A キーなので特殊な用途には使えない
            if vk==0:
                for func in self.hook_call_list:
                    func()
                self.hook_call_list = []
                return True

        self._updateFocusWindow()

        self._fixFunnyModifierState()

        self._recordKey( vk, False )

        try:
            vk = self.vk_vk_map[vk]
            replaced = True
        except KeyError:
            replaced = False

        #self._debugKeyState(vk)

        if self.last_keydown != vk:
            self.last_keydown = vk
            self.oneshot_canceled = False

        try:
            old_modifier = self.modifier
            if vk in self.vk_mod_map:
                self.modifier |= self.vk_mod_map[vk]
                if self.vk_mod_map[vk] & MODKEY_USER_ALL:
                    key = KeyCondition( vk, old_modifier, up=False )
                    self._keyAction(key)
                    return True

            key = KeyCondition( vk, old_modifier, up=False )

            if self._keyAction(key):
                return True
            elif replaced:
                key_seq = [ ckit.KeyDown(vk) ]
                if self.debug : print( "REP :", key_seq )
                ckit.Input.send(key_seq)
                return True
            else:
                if self.send_input_on_tru:
                    # 一部の環境でモディファイアが押しっぱなしになってしまう現象の回避テスト
                    # TRU でも Input.send すると問題が起きない
                    key_seq = [ ckit.KeyDown(vk) ]
                    if self.debug : print( "TRU :", key_seq )
                    ckit.Input.send(key_seq)
                    return True
                else:
                    if self.debug : print( "TRU :", key )
                    return False

        except Exception as e:
            print( ckit.strings["error_unexpected"], "_onKeyDown" )
            print( e )
            traceback.print_exc()

    def _onKeyUp( self, vk ):

        self._updateFocusWindow()

        self._fixFunnyModifierState()

        self._recordKey( vk, True )

        try:
            vk = self.vk_vk_map[vk]
            replaced = True
        except KeyError:
            replaced = False

        #self._debugKeyState(vk)

        oneshot = ( vk == self.last_keydown and not self.oneshot_canceled )
        self.last_keydown = None
        self.oneshot_canceled = False

        try: # for error
            try: # for oneshot
                if vk in self.vk_mod_map:

                    self.modifier &= ~self.vk_mod_map[vk]

                    if self.vk_mod_map[vk] & MODKEY_USER_ALL:
                        key = KeyCondition( vk, self.modifier, up=True )
                        self._keyAction(key)
                        return True

                key = KeyCondition( vk, self.modifier, up=True )

                if oneshot:
                    oneshot_key = KeyCondition( vk, self.modifier, up=False, oneshot=True )

                if self._keyAction(key):
                    return True
                elif replaced or ( oneshot and self._hasKeyAction(oneshot_key) ):
                    key_seq = [ ckit.KeyUp(vk) ]
                    if self.debug : print( "REP :", key_seq )
                    ckit.Input.send(key_seq)
                    return True
                else:
                    if self.send_input_on_tru:
                        # 一部の環境でモディファイアが押しっぱなしになってしまう現象の回避テスト
                        # TRU でも Input.send すると問題が起きない
                        key_seq = [ ckit.KeyUp(vk) ]
                        if self.debug : print( "TRU :", key_seq )
                        ckit.Input.send(key_seq)
                        return True
                    else:
                        if self.debug : print( "TRU :", key )
                        return False

            finally:
                # ワンショットモディファイア は Up を処理した後に実行する
                # モディファイアキーの Up -> Down を偽装しなくてよいように。
                # Up を処理する前に Up -> Down を偽装すると、他のウインドウで
                # モディファイアが押しっぱなしになるなどの問題があるようだ。
                if oneshot:
                    key = KeyCondition( vk, self.modifier, up=False, oneshot=True )
                    self._keyAction(key)

        except Exception as e:
            print( ckit.strings["error_unexpected"], "_onKeyUp" )
            print( e )
            traceback.print_exc()

    def enterMultiStroke( self, keymap ):

        self.multi_stroke_keymap = keymap
        self._updateKeymap(self.focus)

        help_string = self.multi_stroke_keymap.helpString()
        if help_string:
            self.popBalloon( "MultiStroke", help_string )

    def leaveMultiStroke(self):

        if self.multi_stroke_keymap:
            self.multi_stroke_keymap = None
            self._updateKeymap(self.focus)

            self.closeBalloon( "MultiStroke" )

    def _updateKeymap( self, focus ):

        self.current_map = {}

        if self.multi_stroke_keymap:
            self.current_map.update(self.multi_stroke_keymap.keymap)
        else:
            for window_keymap in self.window_keymap_list:
                if window_keymap.check(focus):
                    self.current_map.update(window_keymap.keymap)


    def _focusChanged( self, focus ):
        try:
            if self.debug:
                if focus:
                    print( "" )
                    print( "Window : app : %s" % ckit.getApplicationNameByPid(focus.pid) )
                    print( "" )
                else:
                    print( "Window : None" )

            self.focus = None
            self._updateKeymap(focus)
            self.focus = focus

        except Exception as e:
            print( ckit.strings["error_unexpected"], "_focusChanged" )
            print( e )
            traceback.print_exc()

    def _hook_onKeyDown( self, vk, scan ):

        # キーフック強制解除検出カウンタをリセット
        self.sanity_check_count = 0

        if self.profile:
            result = [None]
            profile.runctx( "result[0] = self._onKeyDown(vk)", globals(), locals() )
            return result[0]
        else:
            return self._onKeyDown(vk)

    def _hook_onKeyUp( self, vk, scan ):

        # キーフック強制解除検出カウンタをリセット
        self.sanity_check_count = 0

        if self.profile:
            result = [None]
            profile.runctx( "result[0] = self._onKeyUp(vk)", globals(), locals() )
            return result[0]
        else:
            return self._onKeyUp(vk)

    def _hook_onMouseDown( self, x, y, vk ):

        # マウスボタンを操作するとワンショットモディファイアはキャンセルする
        self.oneshot_canceled = True

    def _hook_onMouseUp( self, x, y, vk ):

        # マウスボタンを操作するとワンショットモディファイアはキャンセルする
        self.oneshot_canceled = True


    ## キーの単純な置き換えを指示する
    #
    #  @param self -
    #  @param src  置き換え前のキー
    #  @param dst  置き換え後のキー
    #
    #  引数 src で指定されたキー入力を、引数 dst で指定されたキーの入力として扱うよう指示します。\n
    #  この置き換え処理は、keyhac のキー処理の、もっとも早い段階で行われますので、
    #  キーに機能を割り当てる際は、この置き換え後のキーで記述する必要があります。\n\n
    #
    #  src と dst には、"Space" や "Left" のような文字列形式の識別子か、仮想キーコードを数値で渡します。
    #
    def replaceKey( self, src, dst ):
        try:
            if type(src)==str:
                src = KeyCondition.strToVk(src)
        except:
            print( ckit.strings["error_invalid_expression_for_argument"] % ("src",), src )
            return

        try:
            if type(dst)==str:
                dst = KeyCondition.strToVk(dst)
        except:
            print( ckit.strings["error_invalid_expression_for_argument"] % ("dst",), dst )
            return

        self.vk_vk_map[src] = dst

    ## ユーザモディファイアキーを定義する
    #
    #  @param self -
    #  @param vk   モディファイアとして使用するキー
    #  @param mod  割り当てるモディファイアキー
    #
    #  引数 vk で指定されたキーを、mod で指定されたモディファイアキーとして扱うよう指示します。\n\n
    #
    #  モディファイアキーとは、Shift や Ctrl のように、同時に押しておくことで、
    #  キー入力に別の意味を持たせるためのキーのことです。\n\n
    #
    #  vk には、"Space" や "Left" のような文字列形式の識別子か、仮想キーコードを数値で渡します。\n
    #  mod には、"User0" や "U0" のような文字列形式の識別子を渡します。
    #
    def defineModifier( self, vk, mod ):

        try:
            vk_org = vk
            if type(vk)==str:
                vk = KeyCondition.strToVk(vk)
        except:
            print( ckit.strings["error_invalid_expression_for_argument"] % ("vk",), vk )
            return

        try:
            if type(mod)==str:
                mod = KeyCondition.strToMod( mod, force_LR=True )
            else:
                raise TypeError
        except:
            print( ckit.strings["error_invalid_expression_for_argument"] % ("mod",), mod )
            return

        try:
            if vk in self.vk_mod_map:
                raise ValueError
        except:
            print( ckit.strings["error_already_defined_as_modifier"], vk_org )
            return

        self.vk_mod_map[vk] = mod

    ## 特定条件のウインドウのキーマップを定義する
    #
    #  @param self        -
    #  @param app_name    ウインドウが所属するアプリケーションバンドル名
    #  @param check_func  ウインドウ識別関数
    #  @return ウインドウごとのキーマップ
    #
    #  アプリケーションごと、あるいはウインドウごとに、それぞれ異なったキーのカスタマイズを行うために、
    #  実行ファイル名やウインドウの名前から、ウインドウの識別条件を定義します。\n\n
    #
    #  引数 exe_name, class_name, window_text に渡す文字列は、keyhac のコンソールウインドウを使って、調査することが出来ます。
    #  タスクトレイ中の keyhac のアイコンを右クリックして、[ 内部ログ ON ] を選択すると、コンソールウインドウに、
    #  フォーカス位置のウインドウの詳細情報が出力されるようになります。\n\n
    #
    #  引数 exe_name, class_name, window_text, check_func を省略するか None を渡した場合は、
    #  その条件を無視します。
    #
    #  引数 exe_name, class_name, window_text には、ワイルドカード ( * ? ) を使うことが出来ます。\n
    #
    #  check_func には、pyauto.Window オブジェクトを受け取り、True か False を返す関数を渡します。\n
    #  pyauto.Window クラスについては、pyauto のリファレンスを参照してください。\n
    #  http://hp.vector.co.jp/authors/VA012411/pyauto/doc/
    #
    def defineWindowKeymap( self, app_name=None, check_func=None ):
        window_keymap = WindowKeymap( app_name, check_func )
        self.window_keymap_list.append(window_keymap)
        return window_keymap

    ## マルチストローク用のキーマップを定義する
    def defineMultiStrokeKeymap( self, help_string=None ):
        keymap = WindowKeymap( help_string=help_string )
        return keymap

    def beginInput(self):
        self.input_seq = []
        self.virtual_modifier = self.modifier

    def endInput(self):
        self.setInput_Modifier(self.modifier)
        if self.debug : print( "OUT :", self.input_seq )
        ckit.Input.send(self.input_seq)
        self.input_seq = []

    def setInput_Modifier( self, mod ):

        # Win と Alt の単体押しのキャンセルが必要かチェック
        # Win の単体押しは スタートメニューが開き、Alt の単体押しは メニューバーにフォーカスが移動してしまう。
        cancel_oneshot_win_alt = False
        if ( checkModifier( self.virtual_modifier, MODKEY_ALT ) or checkModifier( self.virtual_modifier, MODKEY_WIN ) ) and mod==0:
            cancel_oneshot_win_alt = True
        elif self.virtual_modifier==0 and ( checkModifier( mod, MODKEY_ALT ) or checkModifier( mod, MODKEY_WIN ) ):
            cancel_oneshot_win_alt = True

        # モディファイア押す
        for vk_mod in self.vk_mod_map.items():
            if vk_mod[1] & MODKEY_USER_ALL : continue
            if not ( vk_mod[1] & self.virtual_modifier ) and ( vk_mod[1] & mod ):
                self.input_seq.append( ckit.KeyDown(vk_mod[0]) )
                self.virtual_modifier |= vk_mod[1]

        # Win と Alt の単体押しをキャンセル
        if cancel_oneshot_win_alt:
            self.input_seq.append( ckit.Key( VK_LCONTROL ) )

        # モディファイア離す
        for vk_mod in self.vk_mod_map.items():
            if vk_mod[1] & MODKEY_USER_ALL : continue
            if ( vk_mod[1] & self.virtual_modifier ) and not ( vk_mod[1] & mod ):
                self.input_seq.append( ckit.KeyUp(vk_mod[0]) )
                self.virtual_modifier &= ~vk_mod[1]

    def setInput_FromString( self, s ):

        s = s.upper()

        vk = None
        mod = 0
        up = None

        token_list = s.split("-")

        for token in token_list[:-1]:

            token = token.strip()

            try:
                mod |= KeyCondition.strToMod( token, force_LR=True )
            except ValueError:
                if token=="D":
                    up = False
                elif token=="U":
                    up = True
                else:
                    raise ValueError

        token = token_list[-1].strip()

        vk = KeyCondition.strToVk(token)

        self.setInput_Modifier(mod)

        if up==True:
            self.input_seq.append( ckit.KeyUp(vk) )
        elif up==False:
            self.input_seq.append( ckit.KeyDown(vk) )
        else:
            self.input_seq.append( ckit.Key(vk) )

    # Win と Alt の単体押しをキャンセルする ( beginInput/endInput込み )
    # Win の単体押しは スタートメニューが開き、Alt の単体押しは メニューバーにフォーカスが移動してしまう。
    def _cancelOneshotWinAlt(self):
        if checkModifier( self.modifier, MODKEY_ALT ) or checkModifier( self.modifier, MODKEY_WIN ):
            self.beginInput()
            self.setInput_Modifier( self.modifier | MODKEY_CTRL_L )
            self.endInput()

    # フォーカスがあるウインドウを明示的に更新する
    def _updateFocusWindow(self):

        new_focus_change_count = ckit.getFocusChangeCount()
        if self.focus_change_count == new_focus_change_count:
            return

        self.focus_change_count = new_focus_change_count

        try:
            systemwide = accessibility.create_systemwide_ref()
            focused_app = systemwide["AXFocusedApplication"]
            focus = focused_app["AXFocusedUIElement"]
        except Exception as e:
            focus = None

        self._focusChanged(focus)

    # モディファイアのおかしな状態を修正する
    # たとえば Win-L を押して ロック画面に行ったときに Winキーが押されっぱなしになってしまうような現象を回避
    def _fixFunnyModifierState(self):

        if ckit.platform()=="win":

            for vk_mod in self.vk_mod_map.items():

                if vk_mod[1] & MODKEY_USER_ALL:
                    continue

                if self.modifier & vk_mod[1]:
                    state = ckit.Input.getAsyncKeyState(vk_mod[0])
                    if not (state & 0x8000):
                        self.modifier &= ~vk_mod[1]
                        if self.debug :
                            print( "" )
                            print( "FIX :", KeyCondition.vkToStr(vk_mod[0]) )
                            print( "" )


    # キーフックが強制解除されたことを検出し、フックを再設定する
    def checkSanity(self):

        if not self.hook_enabled : return

        if ckit.platform()=="win":

            state = [ ckit.Input.getAsyncKeyState(vk_mod[0]) for vk_mod in self.vk_mod_map.items() ]
            if self.sanity_check_state != state:
                self.sanity_check_count += 1
                self.sanity_check_state = state
                if self.sanity_check_count >= 4:
                    print( "" )
                    print( ckit.strings["log_key_hook_force_cancellation_detected"] )
                    print( "" )
                    keyhac_hook.hook.reset()
                    self.sanity_check_count = 0

    ## フックのなかで与えられた関数を実行する
    #
    #  @param self  -
    #  @param func  フックの中で実行する関数
    #
    #  Input.send() で実行する擬似的な入力の中に、物理的な入力を割り込ませないためには、
    #  Input.send() をフックのなかで実行する必要があります。
    #
    #  フックの外で InputKeyCommand などのキー入力機能を実行すると、
    #  稀に物理的なキー入力が擬似的なキー入力のなかに割り込んでしまい、
    #  意図しないキー操作になってしまったり、キーが押しっぱなしになってしまったり、
    #  といった問題が起きる可能性があります。
    #
    def hookCall( self, func ):
        self.hook_call_list.append(func)
        print("Warning : hookCall is not supported on other than Windows.")
        ckit.Input.send( [ ckit.KeyDown(0) ] ) # FIXME : vk=0 は Macでは A キーなので特殊な用途には使えない

    ## キーボードフォーカスを持っているUI要素を取得する
    #
    #  @return キーボードフォーカスを持っている accessibility の UIElement オブジェクト
    #
    def getFocus(self):
        return self.focus

    ## キーボードフォーカスを持っているトップレベルのウインドウを取得する
    #
    #  @return キーボードフォーカスを持っているトップレベルのウインドウを指す accessibility の UIElement オブジェクト
    #
    def getFocusedWindow(self):

        systemwide = accessibility.create_systemwide_ref()

        try:
            focused_app = systemwide["AXFocusedApplication"]
        except Exception as e:
            return None

        try:
            focused_window = focused_app["AXFocusedWindow"]
        except ValueError as e:
            return None

        return focused_window

    ## キーボードフォーカスを持っているアプリケーションのプロセスIDを取得する
    #
    #  @return キーボードフォーカスを持っているアプリケーションのプロセスID
    #
    def getFocusedApplicationPid(self):

        systemwide = accessibility.create_systemwide_ref()

        try:
            focused_app = systemwide["AXFocusedApplication"]
        except Exception as e:
            print(e)
            return None

        return focused_app.pid

    ## バルーンヘルプを開く
    #
    #  @param   self    -
    #  @param   name    バルーンヘルプに付ける名前
    #  @param   text    ヘルプとして表示する文字列
    #  @param   timeout ヘルプを表示する時間(ミリ秒)
    #  @sa      closeBalloon
    #
    def popBalloon( self, name, text, timeout=None ):

        if ckit.platform()=="win":
            # キャレット位置またはフォーカスウインドウの左下位置を取得
            caret_wnd, caret_rect = pyauto.Window.getCaret()
            if caret_wnd:
                pos = caret_wnd.clientToScreen( caret_rect[0], caret_rect[3] )
            else:
                focus_wnd = pyauto.Window.getFocus()
                focus_client_rect = focus_wnd.getClientRect()
                pos = focus_wnd.clientToScreen( focus_client_rect[0], focus_client_rect[3] )
        else:
            # FIXME : 実装
            pos = ( 100, 100 )

        # すでにバルーンがあったら閉じる
        self.closeBalloon()

        self.balloon = keyhac_balloon.BalloonWindow(self)
        self.balloon.setText( pos[0], pos[1], text )
        self.balloon_name = name

        if timeout:
            def onTimerCloseBalloon():
                self.closeBalloon(name)
            self.balloon_timer = onTimerCloseBalloon
            self.setTimer( self.balloon_timer, timeout )

    ## バルーンヘルプを閉じる
    #
    #  @param   self    -
    #  @param   name    バルーンヘルプを識別する文字列
    #  @sa      popBalloon
    #
    def closeBalloon( self, name=None ):

        if name==None or self.balloon_name==name:

            if self.balloon:
                self.balloon.destroy()

            self.balloon = None
            self.balloon_name = ""

            if self.balloon_timer:
                self.killTimer( self.balloon_timer )
                self.balloon_timer = None

    #--------------------------------------------------------
    # ここから下のメソッドはキーに割り当てることができる
    #--------------------------------------------------------


    ## キーを入力する関数を返す
    #
    #  @param self -
    #  @param keys 入力するキーのシーケンス (可変引数)
    #  @return キーを入力する関数
    #
    #  与えられたキーシーケンスを入力する関数を生成し、返します。
    #
    #  引数 keys には、 ( "A-Z", "A-X" ) のような、キー入力の文字列表現を渡します。
    #
    #  キーを入力する機能を持っているのは、この関数から返される関数であり、
    #  この関数自体はその機能を持っていないことに注意が必要です。
    #
    def InputKeyCommand( self, *keys ):

        def _inputKey():

            self.beginInput()

            for item in keys:

                if type(item)==str:
                    self.setInput_FromString(item)
                else:
                    raise TypeError;

            self.endInput()

        return _inputKey


    ## 文字を入力する関数を返す
    #
    #  @param self -
    #  @param s    入力する文字
    #  @return 文字を入力する関数
    #
    #  与えられた文字列を入力する関数を生成し、返します。
    #
    #  文字列を入力する機能を持っているのは、この関数から返される関数であり、
    #  この関数自体はその機能を持っていないことに注意が必要です。
    #
    def InputTextCommand( self, s ):

        def _inputText():

            self.beginInput()

            self.setInput_Modifier(0)

            for c in s:
                self.input_seq.append( pyauto.Char(c) )

            self.endInput()

        return _inputText


    ## キーボードマクロの記録を開始する
    #
    #  @param self -
    #
    def command_RecordStart(self):
        self.record_seq = []
        self.record_status = "recording"
        print( ckit.strings["log_macro_recording_started"] )
        self.popBalloon( "Record", ckit.strings["balloon_macro_recording_started"], 3000 )

    ## キーボードマクロの記録を終了する
    #
    #  @param self -
    #
    def command_RecordStop(self):

        if self.record_status=="recording":

            key_table = [ False ] * 256
            normalized_seq = []

            for vk, up in self.record_seq:
                if not up:
                    key_table[vk] = True
                    normalized_seq.append( [ vk, up, False ] ) # Upが来るまで未確定
                else:
                    if key_table[vk]:
                        key_table[vk] = False

                        for i in range(len(normalized_seq)-1,-1,-1):
                            if normalized_seq[i][0] == vk:
                                if normalized_seq[i][1]:
                                    break
                                else:
                                    normalized_seq[i][2] = True # Downを確定

                        normalized_seq.append( [ vk, up, True ] ) # 確定

            self.record_seq = []
            for vk, up, use in normalized_seq:
                if use:
                    self.record_seq.append( ( vk, up ) )

            self.record_status="recorded"

            print( ckit.strings["log_macro_recording_stopped"] )
            self.popBalloon( "Record", ckit.strings["balloon_macro_recording_stopped"], 3000 )

    ## キーボードマクロの記録を開始または終了する
    #
    #  @param self -
    #
    def command_RecordToggle(self):
        if self.record_status=="recording":
            return self.command_RecordStop()
        else:
            return self.command_RecordStart()

    ## キーボードマクロのを消去する
    #
    #  @param self -
    #
    def command_RecordClear(self):
        self.record_seq = None
        self.record_status = None
        print( ckit.strings["log_macro_recording_cleared"] )
        self.popBalloon( "Record", ckit.strings["balloon_macro_recording_cleared"], 3000 )

    ## キーボードマクロを再生する
    #
    #  @param self -
    #
    def command_RecordPlay(self):

        if self.record_status=="recorded":

            print( ckit.strings["log_macro_replay"] )

            # モディファイアを離す
            modifier = self.modifier
            input_seq = []
            for vk_mod in self.vk_mod_map.items():
                if self.modifier & vk_mod[1]:
                    input_seq.append( ckit.KeyUp(vk_mod[0]) )
            ckit.Input.send(input_seq)
            self.modifier = 0

            # 記録されたキーシーケンスを実行する
            for vk, up in self.record_seq:
                if not up:
                    if not self._onKeyDown(vk):
                        key_seq = [ ckit.KeyDown(vk) ]
                        if self.debug : print( "OUT :", key_seq )
                        ckit.Input.send(key_seq)
                else:
                    if not self._onKeyUp(vk):
                        key_seq = [ ckit.KeyUp(vk) ]
                        if self.debug : print( "OUT :", key_seq )
                        ckit.Input.send(key_seq)

            # モディファイアを戻す
            input_seq = []
            for vk_mod in self.vk_mod_map.items():
                if modifier & vk_mod[1]:
                    input_seq.append( ckit.KeyDown(vk_mod[0]) )
            ckit.Input.send(input_seq)
            self.modifier = modifier

    ## マウスカーソルを移動させる関数を返す
    #
    #  @param self    -
    #  @param delta_x 横方向の移動量(ピクセル単位)
    #  @param delta_y 縦方向の移動量(ピクセル単位)
    #  @return マウスカーソルを移動させる関数
    #
    #  与えられた移動量の、マウスカーソルを移動させる関数を生成し、返します。
    #
    #  マウスカーソルを移動させる機能を持っているのは、この関数から返される関数であり、
    #  この関数自体はその機能を持っていないことに注意が必要です。
    #
    def MouseMoveCommand( self, delta_x, delta_y ):

        def _mouseMove():

            x,y = ckit.Input.getCursorPos()

            x += delta_x
            y += delta_y

            self.beginInput()

            self.input_seq.append( pyauto.MouseMove(x,y) )

            self.endInput()

        return _mouseMove


    ## マウスのボタンを擬似的に押す関数を返す
    #
    #  @param self    -
    #  @param button ボタンを識別する文字列 ( 'left' / 'middle' / 'right' )
    #  @return マウスのボタンを擬似的に押す関数
    #
    #  マウスのボタンを擬似的に押す関数を生成し、返します。
    #
    #  マウスのボタンを擬似的に押す機能を持っているのは、この関数から返される関数であり、
    #  この関数自体はその機能を持っていないことに注意が必要です。
    #
    def MouseButtonDownCommand( self, button='left' ):

        def _mouseButtonDown():

            x,y = ckit.Input.getCursorPos()

            self.beginInput()

            self.setInput_Modifier(0)

            mouse_input = None
            if button=='left':
                mouse_input = pyauto.MouseLeftDown(x,y)
            elif button=='middle':
                mouse_input = pyauto.MouseMiddleDown(x,y)
            elif button=='right':
                mouse_input = pyauto.MouseRightDown(x,y)
            else:
                print( ckit.strings["error_invalid_mouse_button_expression"], button )

            if mouse_input:
                self.input_seq.append( mouse_input )

            self.endInput()

        return _mouseButtonDown


    ## マウスのボタンを擬似的に離す関数を返す
    #
    #  @param self    -
    #  @param button ボタンを識別する文字列 ( 'left' / 'middle' / 'right' )
    #  @return マウスのボタンを擬似的に離す関数
    #
    #  マウスのボタンを擬似的に離す関数を生成し、返します。
    #
    #  マウスのボタンを擬似的に離す機能を持っているのは、この関数から返される関数であり、
    #  この関数自体はその機能を持っていないことに注意が必要です。
    #
    def MouseButtonUpCommand( self, button='left' ):

        def _mouseButtonUp():

            x,y = ckit.Input.getCursorPos()

            self.beginInput()

            self.setInput_Modifier(0)

            mouse_input = None
            if button=='left':
                mouse_input = pyauto.MouseLeftUp(x,y)
            elif button=='middle':
                mouse_input = pyauto.MouseMiddleUp(x,y)
            elif button=='right':
                mouse_input = pyauto.MouseRightUp(x,y)
            else:
                print( ckit.strings["error_invalid_mouse_button_expression"], button )

            if mouse_input:
                self.input_seq.append( mouse_input )

            self.endInput()

        return _mouseButtonUp


    ## マウスのボタンを擬似的にクリックする関数を返す
    #
    #  @param self    -
    #  @param button ボタンを識別する文字列 ( 'left' / 'middle' / 'right' )
    #  @return マウスのボタンを擬似的にクリックする関数
    #
    #  マウスのボタンを擬似的にクリックする関数を生成し、返します。
    #
    #  マウスのボタンを擬似的にクリックする機能を持っているのは、この関数から返される関数であり、
    #  この関数自体はその機能を持っていないことに注意が必要です。
    #
    def MouseButtonClickCommand( self, button='left' ):

        def _mouseButtonClick():

            x,y = ckit.Input.getCursorPos()

            self.beginInput()

            self.setInput_Modifier(0)

            mouse_input = None
            if button=='left':
                mouse_input = pyauto.MouseLeftClick(x,y)
            elif button=='middle':
                mouse_input = pyauto.MouseMiddleClick(x,y)
            elif button=='right':
                mouse_input = pyauto.MouseRightClick(x,y)
            else:
                print( ckit.strings["error_invalid_mouse_button_expression"], button )

            if mouse_input:
                self.input_seq.append( mouse_input )

            self.endInput()

        return _mouseButtonClick


    ## マウスのホイールを擬似的に回転する関数を返す
    #
    #  @param self    -
    #  @param wheel 回転量 (1.0=奥に向かって1クリック、-1.0=手前に向かって1クリック)
    #  @return マウスのホイールを擬似的に回転する関数
    #
    #  マウスのホイールを擬似的に回転する関数を生成し、返します。
    #
    #  マウスのホイールを擬似的に回転する機能を持っているのは、この関数から返される関数であり、
    #  この関数自体はその機能を持っていないことに注意が必要です。
    #
    def MouseWheelCommand( self, wheel ):

        def _mouseWheel():

            x,y = ckit.Input.getCursorPos()

            self.beginInput()

            self.setInput_Modifier(0)

            self.input_seq.append( pyauto.MouseWheel(x,y,wheel) )

            self.endInput()

        return _mouseWheel


    ## マウスの水平ホイールを擬似的に回転する関数を返す
    #
    #  @param self    -
    #  @param wheel 回転量 (1.0=奥に向かって1クリック、-1.0=手前に向かって1クリック)
    #  @return マウスの水平ホイールを擬似的に回転する関数
    #
    #  マウスの水平ホイールを擬似的に回転する関数を生成し、返します。
    #
    #  マウスの水平ホイールを擬似的に回転する機能を持っているのは、この関数から返される関数であり、
    #  この関数自体はその機能を持っていないことに注意が必要です。
    #
    def MouseHorizontalWheelCommand( self, wheel ):

        def _mouseHorizontalWheel():

            x,y = ckit.Input.getCursorPos()

            self.beginInput()

            self.setInput_Modifier(0)

            self.input_seq.append( pyauto.MouseHorizontalWheel(x,y,wheel) )

            self.endInput()

        return _mouseHorizontalWheel


    ## フォーカスされているウインドウを移動させる関数を返す
    #
    #  @param self    -
    #  @param delta_x 横方向の移動量(ピクセル単位)
    #  @param delta_y 縦方向の移動量(ピクセル単位)
    #  @return ウインドウを移動させる関数
    #
    #  与えられた移動量の、ウインドウを移動させる関数を生成し、返します。
    #
    #  ウインドウを移動させる機能を持っているのは、この関数から返される関数であり、
    #  この関数自体はその機能を持っていないことに注意が必要です。
    #
    def MoveWindowCommand( self, delta_x, delta_y ):

        def _moveWindow():

            window = self.getFocusedWindow()
            if not window : return

            pos = list( window["AXPosition"] )
            pos[0] += delta_x
            pos[1] += delta_y
            window["AXPosition"] = tuple(pos)

        return _moveWindow


    ## フォーカスされているウインドウをモニターの端にそろえるように移動させる関数を返す
    #
    #  @param self      -
    #  @param direction 移動方向 (  0:左  1:上  2:右  3:下  )
    #  @return ウインドウを移動させる関数
    #
    #  与えられた方向にウインドウを移動させる関数を生成し、返します。
    #
    #  ウインドウを移動させる機能を持っているのは、この関数から返される関数であり、
    #  この関数自体はその機能を持っていないことに注意が必要です。
    #
    def MoveWindowToMonitorEdgeCommand( self, direction ):

        def _intersectRect( rect1, rect2 ):
            intersect = list(rect1)
            intersect[0] = max( intersect[0], rect2[0] )
            intersect[1] = max( intersect[1], rect2[1] )
            intersect[2] = min( intersect[2], rect2[2] )
            intersect[3] = min( intersect[3], rect2[3] )
            return intersect

        def _moveWindowEdge():

            wnd_top = self.getFocusedWindow()
            if not wnd_top: return

            pos = list(wnd_top["AXPosition"])
            size = wnd_top["AXSize"]
            wnd_rect = [ pos[0], pos[1], pos[0]+size[0], pos[1]+size[1] ]

            nearest = None
            farthest = None
            for monitor in ckit.getMonitorInfo():

                intersect_rect = _intersectRect( monitor[1], wnd_rect )
                intersect = False

                if direction==0 or direction==2:
                    if intersect_rect[1]<intersect_rect[3]:
                        intersect = True
                else:
                    if intersect_rect[0]<intersect_rect[2]:
                        intersect = True

                if intersect:
                    monitor_edge  = monitor[1][direction]
                    if direction<2:
                        if monitor_edge < wnd_rect[direction]:
                            if nearest==None or nearest<monitor_edge:
                                nearest=monitor_edge
                        if farthest==None or farthest>monitor_edge:
                            farthest=monitor_edge
                    else:
                        if monitor_edge > wnd_rect[direction]:
                            if nearest==None or nearest>monitor_edge:
                                nearest=monitor_edge
                        if farthest==None or farthest<monitor_edge:
                            farthest=monitor_edge

            if nearest==None:
                nearest=farthest

            if nearest!=None:
                delta = nearest-wnd_rect[direction]
                if direction==0 or direction==2:
                    pos[0] += delta
                else:
                    pos[1] += delta
                wnd_top["AXPosition"] = tuple(pos)

        return _moveWindowEdge

    ## アプリケーションをアクティブ化する
    #
    #  @param self -
    #  @param app_name アプリケーションの名前 (例: "com.apple.Terminal" )
    #
    def activateApplication( self, app_name ):
        pid_found = None
        for pid in ckit.getRunningApplications():
            if app_name == ckit.getApplicationNameByPid(pid):
                pid_found = pid
                break
        if pid_found:
            ckit.activateApplicationByPid(pid_found)
        else:
            raise ValueError

    ## アプリケーションをアクティブ化するコマンド
    #
    #  @param self -
    #  @param app_name アプリケーションの名前 (例: "com.apple.Terminal" )
    #
    def ActivateApplicationCommand( self, app_name ):
        def _activateApplication():
            try:
                self.activateApplication(app_name)
            except ValueError:
                print( "ERROR : 指定されたアプリケーションがありません :", app_name )
        return _activateApplication

    ## サブプロセスを非同期に呼び出す関数を返す
    #
    #  @param self -
    #  @param cmd           コマンドと引数の文字列を格納した配列
    #  @param cwd           作業ディレクトリ
    #  @param env           環境変数を格納した辞書
    #  @return サブプロセスを非同期に呼び出す関数
    #
    #  指定されたコマンドをサブプロセスとして呼び出す関数を生成し、返します。
    #
    #  cmd には、[ "open", "-a", "Safari" ] のように、コマンドラインを文字列に分割した配列を渡します。
    #
    #  サブプロセスの起動は、非同期にサブスレッドの中で行われます。
    #
    def SubProcessCallCommand( self, cmd, cwd=None, env=None ):

        def _subProcessCall():

            def jobSubProcessCall(job_item):
                p = ckit.SubProcess(cmd,cwd,env)
                p()

            def jobSubProcessCallFinished(job_item):
                pass

            job_item = ckit.JobItem( jobSubProcessCall, jobSubProcessCallFinished )
            ckit.JobQueue.defaultQueue().enqueue(job_item)

        return _subProcessCall


    ## リストウインドウを開き結果を取得する
    #
    #  @param   self     -
    #  @param   listers  リストのアイテムを列挙するオブジェクトのリスト
    #  @return 選択されたアイテム(キャンセル時 None) と モディファイアキー のタプル
    #
    def popListWindow( self, listers ):

        # １つしか開かない
        if self.list_window:
            return None, 0

        paste_target_pid = self.getFocusedApplicationPid()

        monitor_info_list = ckit.getMonitorInfo()
        focus_rect = monitor_info_list[0][0]
        uielm = self.focus

        while uielm:
            try:
                focus_pos = uielm["AXPosition"]
                focus_size = uielm["AXSize"]
                focus_rect = ( max(focus_pos[0],focus_rect[0]), max(focus_pos[1],focus_rect[1]), min(focus_pos[0]+focus_size[0],focus_rect[2]), min(focus_pos[1]+focus_size[1],focus_rect[3]) )
                uielm = uielm["AXParent"]
            except (KeyError, ValueError) as e:
                break

        focus_rect = ( int(focus_rect[0]), int(focus_rect[1]), int(focus_rect[2]), int(focus_rect[3]) )

        lister_select = 0

        def onStatusMessage( width, select ):
            return ""

        while True:

            # ちらつきを防止するために ListWindow の破棄を遅延する
            list_window_old = self.list_window

            title, lister = listers[lister_select]

            items = lister.getListItems()

            max_width = 80
            max_height = 16

            def onKeyDown( vk, mod ):

                if vk==VK_LEFT and mod==0:
                    if lister_select-1>=0:
                        self.list_window.switch_left = True
                        self.list_window.quit()
                    return True

                elif vk==VK_RIGHT and mod==0:
                    if lister_select+1<len(listers):
                        self.list_window.switch_right = True
                        self.list_window.quit()
                    return True

                elif vk==VK_DELETE and mod==0:
                    if hasattr(lister,"remove"):
                        select, mod = self.list_window.getResult()
                        lister.remove(select)
                        self.list_window.remove(select)
                    return True

            if ckit.platform()=="win":
                # 親ウインドウをフォアグラウンドにしないと、ListWindowがフォアグラウンドに来ない
                pyauto.Window.fromHWND( self.getHWND() ).setForeground(True)
            else:
                self.foreground()

            self.list_window = keyhac_listwindow.ListWindow( (focus_rect[0]+focus_rect[2])//2, (focus_rect[1]+focus_rect[3])//2, 5, 1, max_width, max_height, self, False, title, items, initial_select=0, return_modkey=True, keydown_hook=onKeyDown, onekey_search=False, statusbar_handler=onStatusMessage )
            self.list_window.lister = lister
            self.list_window.switch_left = False
            self.list_window.switch_right = False

            if focus_rect[3] - focus_rect[1] < 64:
                keyhac_misc.adjustWindowPosition( self.list_window, focus_rect )

            self.list_window.show(True,True)

            if list_window_old:
                list_window_old.destroy()

            self.list_window.messageLoop()

            if self.list_window.switch_left:
                if lister_select-1>=0:
                    lister_select -= 1
            elif self.list_window.switch_right:
                if lister_select+1<len(listers):
                    lister_select += 1
            else:
                break

        if paste_target_pid:
            ckit.activateApplicationByPid(paste_target_pid)

        select, mod = self.list_window.getResult()
        self.list_window.destroy()
        self.list_window = None

        if select<0 :
            return None, 0

        return items[select], mod

    ## リストウインドウをキャンセルで閉じる
    def cancelListWindow(self):
        if self.list_window:
            self.list_window.cancel()

    ## リストウインドウが存在しているかを確認する
    def isListWindowOpened(self):
        return (self.list_window!=None)

    ## クリップボード履歴をリストウインドウで表示する
    def popClipboardList(self):

        item, mod = self.popListWindow( self.cblisters )
        if item==None : return

        if callable(item[1]):
            command = item[1]
            text = command()
        else:
            text = item[1]

        if not text: return

        # Cmdを押しながら決定したときは、引用記号付で貼り付ける
        if mod & MODKEY_CMD:
            lines = text.splitlines(True)
            text = ""
            for line in lines:
                text += self.quote_mark + line

        def jobPaste(job_item):

            # フォーカスアプリケーションがNoneでなくなるのを待つ
            wnd = None
            timeout = 0.5
            retry_time = 0.01
            while timeout>0.0:
                pid = self.getFocusedApplicationPid()
                if wnd==None:
                    time.sleep(retry_time)
                    timeout -= retry_time
                    continue
                break

        def jobPasteFinished(job_item):
            ckit.setClipboardText(text)
            if ckit.platform()=="win":
                self.hookCall( self.InputKeyCommand("Ctrl-V") )
            else:
                self.InputKeyCommand("Cmd-V")()

        # Shiftを押しながら決定したときは、貼り付けを行わず、クリップボードに格納するだけ
        if mod & MODKEY_SHIFT:
            ckit.setClipboardText(text)
        else:
            job_item = ckit.JobItem( jobPaste, jobPasteFinished )
            ckit.JobQueue.defaultQueue().enqueue(job_item)

    ## クリップボード履歴をリスト表示する
    #
    #  @param self -
    #
    #  クリップボード履歴をリスト表示します。
    #
    #  クリップボード履歴を選択し、Enterキーを押すことで、ペーストすることが出来ます。
    #  Fキーを押して、インクリメンタルサーチをすることが出来ます。
    #
    def command_ClipboardList(self):

        # すでに開いていたら閉じる
        if self.isListWindowOpened():
            self.cancelListWindow()
            return

        # キーボードフックの中で長時間処理できないので、DelayedCallをつかってリストを表示する
        self.delayedCall( self.popClipboardList, 0 )


    ## クリップボード履歴の直近のアイテムを削除する
    #
    #  @param self -
    #
    #  クリップボード履歴の直近のアイテムを削除して、一つ前のアイテムをアクティブにします。
    #
    def command_ClipboardRemove(self):
        self.cancelListWindow()
        self.clipboard_history.pop()


    ## クリップボード履歴の直近のアイテムを末尾に回す
    #
    #  @param self -
    #
    #  クリップボード履歴の直近のアイテムを末尾に回して、一つ前のアイテムをアクティブにします。
    #
    def command_ClipboardRotate(self):
        self.cancelListWindow()
        self.clipboard_history.rotate()

    ## config.py を再読み込みする
    def command_ReloadConfig(self):
        self.configure()
        print( ckit.strings["log_config_reloaded"] )
        print( "" )

    ## config.py を編集する
    def command_EditConfig(self):

        def jobEditConfig(job_item):
            self.editConfigFile()

        def jobEditConfigFinished(job_item):
            print( ckit.strings["log_config_editor_launched"] )
            print( "" )

        job_item = ckit.JobItem( jobEditConfig, jobEditConfigFinished )
        ckit.JobQueue.defaultQueue().enqueue(job_item)

## @} keymap
