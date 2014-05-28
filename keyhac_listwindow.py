import sys
import os

import ckit
from ckit.ckit_const import *

import keyhac_isearch
import keyhac_statusbar
import keyhac_misc


## @addtogroup listwindow
## @{

#--------------------------------------------------------------------

## リストウインドウ
#
#  各種のリスト形式ウインドウを実現しているクラスです。\n\n
#  設定ファイル config.py の configure_ListWindow に渡される window 引数は、ListWindow クラスのオブジェクトです。
#
class ListWindow( ckit.TextWindow ):

    def __init__( self, x, y, min_width, min_height, max_width, max_height, parent_window, show=True, title="", items=[], initial_select=0, onekey_search=True, onekey_decide=False, return_modkey=False, keydown_hook=None, statusbar_handler=None ):

        ckit.TextWindow.__init__(
            self,
            x=x,
            y=y,
            width=5,
            height=5,
            origin= ORIGIN_X_CENTER | ORIGIN_Y_CENTER,
            parent_window=parent_window,
            bg_color = ckit.getColor("bg"),
            show = False,
            resizable = False,
            title = title,
            minimizebox = False,
            maximizebox = False,
            close_handler = self.onClose,
            keydown_handler = self.onKeyDown,
            char_handler = self.onChar,
            )

        max_item_width = 0
        for item in items:
            if isinstance(item,list) or isinstance(item,tuple):
                item = item[0]
            item_width = self.getStringWidth(item)
            if item_width>max_item_width:
                max_item_width=item_width

        window_width = max_item_width
        window_height = len(items)
        
        if statusbar_handler:
            window_height += 1
        
        window_width = min(window_width,max_width)
        window_height = min(window_height,max_height)

        window_width = max(window_width,min_width)
        window_height = max(window_height,min_height)

        self.setPosSize(
            x=x,
            y=y,
            width=window_width,
            height=window_height,
            origin= ORIGIN_X_CENTER | ORIGIN_Y_CENTER
            )
        self.show(show)

        self.title = title
        self.items = items
        self.scroll_info = ckit.ScrollInfo()
        self.select = initial_select
        self.result_mod = 0
        self.onekey_search = onekey_search
        self.onekey_decide = onekey_decide
        self.return_modkey = return_modkey
        self.keydown_hook = keydown_hook
        self.statusbar_handler = statusbar_handler

        self.status_bar = None
        self.status_bar_layer = None
        self.skin_statusbar = None

        self.isearch = None
        self.skin_isearch = None
        
        if statusbar_handler:
            self.status_bar = keyhac_statusbar.StatusBar()
            self.status_bar_layer = keyhac_statusbar.SimpleStatusBarLayer()
            self.status_bar.registerLayer(self.status_bar_layer)

            self.skin_statusbar = ckit.ThemePlane3x3( self, 'bar.png' )
            client_rect = self.getClientRect()
            pos1 = self.charToClient( 0, self.height()-1 )
            char_w, char_h = self.getCharSize()
            self.skin_statusbar.setPosSize( pos1[0]-char_w//2, pos1[1], self.width()*char_w+char_w, client_rect[3]-pos1[1] )
            self.skin_statusbar.show(True)

            self.skin_isearch = ckit.ThemePlane3x3( self, 'edit.png', 1 )
            self.skin_isearch.setPosSize( pos1[0]-char_w//2, pos1[1], self.width()*char_w+char_w, client_rect[3]-pos1[1] )
            self.skin_isearch.show(False)

        self.scroll_info.makeVisible( self.select, self.itemsHeight() )
        
        self.configure()
        
        self.paint()

    ## 設定を読み込む
    #
    #  キーマップなどをリセットした上で、config,py の configure_ListWindow() を呼び出します。
    #
    def configure(self):
        self.keymap = ckit.Keymap()
        self.keymap[ "Up" ] = self.command_CursorUp
        self.keymap[ "Down" ] = self.command_CursorDown
        self.keymap[ "PageUp" ] = self.command_CursorPageUp
        self.keymap[ "PageDown" ] = self.command_CursorPageDown
        self.keymap[ "Return" ] = self.command_Enter
        self.keymap[ "C-Return" ] = self.command_Enter
        self.keymap[ "S-Return" ] = self.command_Enter
        self.keymap[ "C-S-Return" ] = self.command_Enter
        self.keymap[ "Escape" ] = self.command_Cancel
        if not self.onekey_search:
            self.keymap[ "F" ] = self.command_IncrementalSearch
        ckit.callConfigFunc("configure_ListWindow",self)

    ## リストの項目を再設定する
    #
    #  @param self  -
    #  @param items 新しいアイテムのリスト
    #
    def setItems( self, items ):
        
        old_select = self.select
        old_select_item = self.items[old_select]
        
        self.items = items
        
        if not len(self.items):
            self.select = -1
            self.quit()
            return

        self.select -= 1
        for i in range(len(self.items)):
            if self.items[i]==old_select_item:
                self.select = i
                break
        
        if self.select<0 : self.select=0

        self.scroll_info.makeVisible( self.select, self.itemsHeight() )

        self.paint()
        

    ## リストの項目を１つ削除する
    #
    #  @param self  -
    #  @param index 削除する項目のインデックス
    #
    def remove( self, index ):
        
        del self.items[index]
        
        if self.select > index:
            self.select -= 1
        
        if not len(self.items):
            self.select = -1
            self.quit()
            return

        if self.select<0 : self.select=0
        if self.select>len(self.items)-1 : self.select=len(self.items)-1

        self.scroll_info.makeVisible( self.select, self.itemsHeight() )

        self.paint()

    def onClose(self):
        self.quit()

    def onKeyDown( self, vk, mod ):

        if self.keydown_hook:
            if self.keydown_hook( vk, mod ):
                return True

        try:
            func = self.keymap.table[ ckit.KeyEvent(vk,mod) ]
        except KeyError:
            return

        self.result_mod = mod
        func()

        return True

    def onChar( self, ch, mod ):

        if self.onekey_search:
        
            if 0x20<=ch<0x100:
                if not len(self.items): return

                found = []
                
                for i in range( self.select+1, len(self.items) ):

                    item = self.items[i]
                    if isinstance(item,list) or isinstance(item,tuple):
                        item = item[0]

                    if item[0].upper()==chr(ch).upper():
                        found.append(i)
                        if len(found)>=2 : break

                if len(found)<2 :
                    for i in range( self.select+1 ):

                        item = self.items[i]
                        if isinstance(item,list) or isinstance(item,tuple):
                            item = item[0]

                        if item[0].upper()==chr(ch).upper():
                            found.append(i)
                            if len(found)>=2 : break

                if found:
                    if self.onekey_decide and len(found)==1:
                        self.select = found[0]
                        self.result_mod = mod
                        self.quit()
                    else:
                        self.select = found[0]
                        self.scroll_info.makeVisible( self.select, self.itemsHeight() )
                        self.paint()

        else:
            
            if self.isearch:

                if ch==ord('\b'):
                    isearch_newvalue = self.isearch.isearch_value[:-1]
                elif ch==ord(' '):
                    return
                else:
                    isearch_newvalue = self.isearch.isearch_value + chr(ch)

                accept = False

                item = self.items[self.select]
                if isinstance(item,list) or isinstance(item,tuple):
                    item = item[0]
            
                if self.isearch.fnmatch( item, isearch_newvalue ):
                    accept = True
                else:
                
                    if self.isearch.isearch_type=="inaccurate":
                        isearch_type_list = [ "strict", "partial", "inaccurate" ]
                    else:
                        isearch_type_list = [ "strict", "partial", "migemo" ]
                
                    last_type_index = isearch_type_list.index(self.isearch.isearch_type)
                    for isearch_type_index in range(last_type_index+1):
                        for i in range( len(self.items) ):
                        
                            item = self.items[i]
                            if isinstance(item,list) or isinstance(item,tuple):
                                item = item[0]
                        
                            if self.isearch.fnmatch( item, isearch_newvalue, isearch_type_list[isearch_type_index] ):
                                self.select = i
                                self.scroll_info.makeVisible( self.select, self.itemsHeight() )
                                accept = True
                                break
                        if accept: break
        
                if accept:
                    self.isearch.isearch_value = isearch_newvalue
                    self.paint()


    def itemsHeight(self):
        if self.status_bar:
            return self.height()-1
        else:
            return self.height()

    def paint(self):

        x=0
        y=0
        width=self.width()
        height=self.itemsHeight()

        attribute_normal = ckit.Attribute( fg=ckit.getColor("fg") )
        attribute_normal_selected = ckit.Attribute( fg=ckit.getColor("select_fg"), bg=ckit.getColor("select_bg") )
        
        for i in range(height):
            index = self.scroll_info.pos+i
            if index < len(self.items):

                item = self.items[index]
                if isinstance(item,list) or isinstance(item,tuple):
                    item = item[0]

                if self.select==index:
                    attr=attribute_normal_selected
                else:
                    attr=attribute_normal
                self.putString( x, y+i, width, 1, attr, " " * width )
                self.putString( x, y+i, width, 1, attr, item )
            else:
                self.putString( x, y+i, width, 1, attribute_normal, " " * width )

        if self.status_bar:
            if self.isearch:
                attr = ckit.Attribute( fg=ckit.getColor("fg") )
                s = " Search : %s_" % ( self.isearch.isearch_value )
                s = ckit.adjustStringWidth(self,s,width-1)
                self.putString( 0, self.height()-1, width-1, 1, attr, s )
            else:
                self.status_bar_layer.setMessage( self.statusbar_handler( width, self.select ) )
                self.status_bar.paint( self, 0, self.height()-1, width, 1 )
        else:
            if self.isearch:
                s = " Search : %s_" % ( self.isearch.isearch_value )
                self.setTitle(s)
            else:
                self.setTitle(self.title)

    def getResult(self):
        if self.return_modkey:
            return self.select, self.result_mod
        else:
            return self.select

    ## リストウインドウをキャンセルして閉じる
    def cancel(self):
        self.select = -1
        self.quit()

    #--------------------------------------------------------
    # ここから下のメソッドはキーに割り当てることができる
    #--------------------------------------------------------

    ## カーソルを1つ上に移動させる
    def command_CursorUp(self):
        self.select -= 1
        if self.select<0 : self.select=0
        self.scroll_info.makeVisible( self.select, self.itemsHeight() )
        self.paint()

    ## カーソルを1つ下に移動させる
    def command_CursorDown(self):
        self.select += 1
        if self.select>len(self.items)-1 : self.select=len(self.items)-1
        self.scroll_info.makeVisible( self.select, self.itemsHeight() )
        self.paint()

    ## 1ページ上方向にカーソルを移動させる
    def command_CursorPageUp(self):
        if self.isearch:
            return
        if self.select>self.scroll_info.pos :
            self.select = self.scroll_info.pos
        else:
            self.select -= self.itemsHeight()
            if self.select<0 : self.select=0
            self.scroll_info.makeVisible( self.select, self.itemsHeight() )
        self.paint()

    ## 1ページ下方向にカーソルを移動させる
    def command_CursorPageDown(self):
        if self.isearch:
            return
        if self.select<self.scroll_info.pos+self.itemsHeight()-1:
            self.select = self.scroll_info.pos+self.itemsHeight()-1
        else:
            self.select += self.itemsHeight()
        if self.select>len(self.items)-1 : self.select=len(self.items)-1
        self.scroll_info.makeVisible( self.select, self.itemsHeight() )
        self.paint()

    ## 決定する
    def command_Enter(self):
        self.quit()

    ## キャンセルする
    def command_Cancel(self):
        self.cancel()

    ## インクリメンタルサーチを行う
    def command_IncrementalSearch(self):
        
        def finish():
            if self.skin_isearch:
                self.skin_isearch.show(False)
            self.isearch = None
            self.keydown_hook = self.keydown_hook_old
            self.scroll_info.makeVisible( self.select, self.itemsHeight() )
            self.paint()

        def getString(i):
            item = self.items[i]
            if isinstance(item,list) or isinstance(item,tuple):
                item = item[0]
            return item    

        def cursorUp():
            self.select = self.isearch.cursorUp( getString, len(self.items), self.select, self.scroll_info.pos, self.itemsHeight() )
            self.scroll_info.makeVisible( self.select, self.itemsHeight() )
            self.paint()

        def cursorDown():
            self.select = self.isearch.cursorDown( getString, len(self.items), self.select, self.scroll_info.pos, self.itemsHeight() )
            self.scroll_info.makeVisible( self.select, self.itemsHeight() )
            self.paint()

        def cursorPageUp():
            self.select = self.isearch.cursorPageUp( getString, len(self.items), self.select, self.scroll_info.pos, self.itemsHeight() )
            self.scroll_info.makeVisible( self.select, self.itemsHeight() )
            self.paint()

        def cursorPageDown():
            self.select = self.isearch.cursorPageDown( getString, len(self.items), self.select, self.scroll_info.pos, self.itemsHeight() )
            self.scroll_info.makeVisible( self.select, self.itemsHeight() )
            self.paint()

        def onKeyDown( vk, mod ):

            if vk==VK_RETURN:
                finish()
            elif vk==VK_ESCAPE:
                finish()
            elif vk==VK_UP:
                cursorUp()
            elif vk==VK_DOWN:
                cursorDown()
            elif vk==VK_PRIOR:
                cursorPageUp()
            elif vk==VK_NEXT:
                cursorPageDown()

            return True
    
        self.removeKeyMessage()

        self.isearch = keyhac_isearch.IncrementalSearch("migemo")
        self.keydown_hook_old = self.keydown_hook
        self.keydown_hook = onKeyDown
        if self.skin_isearch:
            self.skin_isearch.show(True)
        self.scroll_info.makeVisible( self.select, self.itemsHeight() )
        self.paint()

## @} listwindow

