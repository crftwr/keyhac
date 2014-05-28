import ckit
from ckit.ckit_const import *

#--------------------------------------------------------------------

class BalloonWindow( ckit.TextWindow ):

    def __init__( self, parent_window ):

        ckit.TextWindow.__init__(
            self,
            x=0,
            y=0,
            width=5,
            height=5,
            origin= ORIGIN_X_LEFT | ORIGIN_Y_TOP,
            parent_window=parent_window,
            bg_color = (255,255,255),
            frame_color = (0,0,0),
            border_size = 0,
            show = False,
            resizable = False,
            title_bar = False,
            minimizebox = False,
            maximizebox = False,
            ncpaint = True,
            )
            
        self.enable(False)    
        self.topmost(True)

        self.text = ""

    def setText( self, x, y, text ):
    
        self.text = text

        width = self.getStringWidth(self.text)
        height = 1

        if not self.text:
            self.show( False, False )

        self.setPosSize(
            x=x,
            y=y,
            width=width,
            height=height,
            origin= ORIGIN_X_LEFT | ORIGIN_Y_TOP
            )

        if self.text:
            self.show( True, False )

        self.paint()

    def paint(self):

        x=0
        y=0
        width=self.width()
        height=self.height()

        attr = ckit.Attribute( fg=(0,0,0), bg=(255,255,255) )
        self.putString( x, y, width, 1, attr, self.text )
