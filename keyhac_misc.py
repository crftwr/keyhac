import ckit
from ckit.ckit_const import *

## @addtogroup misc
## @{

#--------------------------------------------------------------------

## ウインドウの位置を調整する
def adjustWindowPosition( new_window, base_rect, default_up=False, monitor_adjust_vertical=True, monitor_adjust_horizontal=True ):

    x1 = base_rect[0]
    x2 = base_rect[2]
    y1 = base_rect[1]
    y2 = base_rect[3]
    
    if default_up:
        y = y1
        origin_y = ORIGIN_Y_BOTTOM
    else:
        y = y2
        origin_y = ORIGIN_Y_TOP

    x = x1
    origin_x = ORIGIN_X_LEFT
           
    monitor_info_list = ckit.getMonitorInfo()
    for monitor_info in monitor_info_list:
        if monitor_info[0][0] <= x1 < monitor_info[0][2] and monitor_info[0][1] <= y1 < monitor_info[0][3]:
            
            new_window_rect = new_window.getWindowRect()
            
            if monitor_adjust_vertical:
                if default_up:
                    if y1 - (new_window_rect[3]-new_window_rect[1]) < monitor_info[1][1]:
                        y = y2
                        origin_y = ORIGIN_Y_TOP
                else:
                    if y2 + (new_window_rect[3]-new_window_rect[1]) >= monitor_info[1][3]:
                        y = y1
                        origin_y = ORIGIN_Y_BOTTOM
            
            if monitor_adjust_horizontal:
                if x1 + (new_window_rect[2]-new_window_rect[0]) >= monitor_info[1][2]:
                    x = x2
                    origin_x = ORIGIN_X_RIGHT
            break

    new_window.setPosSize( x, y, new_window.width(), new_window.height(), origin_x | origin_y )

## @} misc

