import ctypes
import json

import ckit

import keyhac_hook
import keyhac_misc
import keyhac_ini


## @addtogroup clipboardlist
## @{

#--------------------------------------------------------------------

## クリップボードの履歴を管理し、リスト表示するためのクラス
class cblister_ClipboardHistory:
    
    def __init__( self, window, debug=False ):
        
        self.window = window
        self.items = []
        self.maxnum = 1000
        self.quota = 10 * 1024 * 1024
        
        self.debug = debug

        self.insane_count = 0
        self.seq_number = None
        
        keyhac_hook.hook.clipboard = self._hook_onClipboardChanged
        
    def destroy(self):

        keyhac_hook.hook.clipboard = None

        self.save()

    def enableDebug( self, enable ):
        self.debug = enable
        
    def _hook_onClipboardChanged(self):
    
        def onClipboardChanged():
            self._push( ckit.getClipboardText() )
            self.seq_number = ckit.getClipboardSequenceNumber()
        
        # Firefox で クリップボードへの格納が失敗してしまわないように
        # フックの中ではなく、delayedCall で遅延して取得する。
        self.window.delayedCall( onClipboardChanged, 0 )
    
    # クリップボード履歴の内容をOSのクリップボードに反映させる
    def _apply(self):

        if len(self.items):
            text = self.items[0]
        else:
            text = ""
        
        ckit.setClipboardText(text)
    
    # クリップボード履歴にテキストを登録する ( OSのクリップボードに反映させない )
    def _push( self, text ):

        for i in range(len(self.items)):
            if self.items[i]==text:
                del self.items[i]
                break

        if len(self.items)>0:
            if self.items[0]==None or len(self.items[0].strip())==0:
                del self.items[0]

        self.items.insert( 0, text )

        if len(self.items)>self.maxnum:
            self.items = self.items[:self.maxnum]
        
    ## クリップボード履歴にテキストを登録する
    def push( self, text ):
        self._push(text)
        self._apply()    
        
    ## クリップボード履歴の直近の項目を削除する
    def pop(self):
        if len(self.items):
            del self.items[0]
            self._apply()    

    ## クリップボード履歴の直近の項目を末尾に回す
    def rotate(self):

        if len(self.items)>=2:

            self.items = self.items[1:] + [ self.items[0] ]

            if self.items[-1]==None or len(self.items[-1].strip())==0:
                del self.items[-1]

            self._apply()

    ## クリップボード履歴から、指定したインデックス位置の項目を削除する
    def remove( self, index ):
        del self.items[index]
        self._apply()

    ## クリップボード履歴をListWindowで表示するためのリストを返す
    #
    #  返されるリストの項目は、以下のような２つの文字列のタプルになっています。
    #    ( 表示のための先頭部分の文字列, 貼り付けるための全体の文字列 )
    #
    def getListItems(self):

        items = list( self.items[:] )
        
        if len(items)==0:
            return []

        def getLeadingText( s, num_char ):
            s = ' '.join(s.split())
            s = s[:num_char]
            return s

        def build_item(src):
            if src==None:
                return ( "", src )
            return ( getLeadingText(src,100), src )
        items = list( map( build_item, items ) )
        
        return items

    def save(self):

        total_size = 0
        i=0

        while i<len(self.items):
            if self.items[i]:
                item = self.items[i]
            else:
                item = ""
            total_size += len(item) * 2
            if total_size>self.quota: break
            item = json.dumps(item)
            keyhac_ini.set( "CLIPBOARD", "history_%d"%(i,), item )
            i+=1

        while True:
            if not keyhac_ini.remove_option( "CLIPBOARD", "history_%d"%(i,) ) : break
            i+=1

    def load(self):

        total_size = 0

        for i in range(self.maxnum):
            try:
                item = keyhac_ini.get( "CLIPBOARD", "history_%d"%(i,) )
                item = json.loads(item)
                total_size += len(item) * 2
                if total_size>self.quota: break
                self.items.append(item)
            except Exception as e:
                break

        self._push( ckit.getClipboardText() )

    def checkSanity(self):
    
        new_seq_number = ckit.getClipboardSequenceNumber()

        if new_seq_number == self.seq_number:
            self.insane_count = 0
        else:
            self.insane_count += 1

        if self.insane_count>=3:
        
            clipboard_text = ckit.getClipboardText()
        
            # クリップボードのフックの再設定
            keyhac_hook.hook.clipboard = None
            keyhac_hook.hook.clipboard = self._hook_onClipboardChanged

            if self.debug:

                message = ( 
                    "----------------------------------\n"
                    "clipboard content mismatch\n"
                    "re-installed clipboard hook\n"
                    "----------------------------------"
                    )

                print( "" )
                print( message )
                print( "" )

            self.insane_count = 0

## クリップボード履歴リストに定型文をリストアップするクラス
class cblister_FixedPhrase:

    def __init__(self,items):
        self.items = items

    def getListItems(self):
        return self.items

## @} clipboardlist

