import os
import machine
import sdcard
from sys import print_exception
import lvgl as lv
spi2 = machine.SPI(2, 5000000, sck=machine.Pin(41), mosi=machine.Pin(40), miso=machine.Pin(42))
sd_cs = machine.Pin(39, machine.Pin.OUT)

label1 = None
label2 = None
label3 = None
bar = None

def init_lcd():
    global label1, label2, label3, bar
    from ili9XXX import ili9341
    import espidf as esp
    from time import sleep
    disp = ili9341(mosi=11, miso=13, clk=12, cs=1, dc=9, rst=34, backlight=10, backlight_on=1, mhz=40, factor=32, hybrid=True, spihost=esp.VSPI_HOST, double_buffer=False)
    scr = lv.scr_act()
    label1 = lv.label(scr)
    label1.set_text('applying update')
    label1.align(lv.ALIGN.CENTER, 0, -50)
    label1.set_style_text_font(lv.font_montserrat_22, 0)
    label1.set_style_text_color(lv.color_make(255, 255, 0), 0)

    label2 = lv.label(scr)
    label2.set_text('DO NOT TURN OFF\n\t\t\tTHE DEVICE')
    label2.set_style_text_font(lv.font_montserrat_22, 0)
    label2.align_to(label1, lv.ALIGN.BOTTOM_MID, 0, 60)
    label2.set_style_text_color(lv.color_make(255, 255, 0), 0)

    label3 = lv.label(scr)
    label3.set_text(lv.SYMBOL.WARNING)
    label3.set_long_mode(lv.label.LONG.CLIP)
    label3.set_style_text_color(lv.color_make(255, 255, 0), 0)
    label3.set_style_text_font(lv.font_montserrat_48, 0)
    label3.align_to(label1, lv.ALIGN.TOP_MID, 0, -60)

    style_bg = lv.style_t()
    style_indic = lv.style_t()

    style_bg.init()
    style_bg.set_border_color(lv.palette_main(lv.PALETTE.BLUE))
    style_bg.set_border_width(2)
    style_bg.set_pad_all(6)
    style_bg.set_radius(6)
    style_bg.set_anim_time(500)

    style_indic.init()
    style_indic.set_bg_opa(lv.OPA.COVER)
    style_indic.set_bg_color(lv.palette_main(lv.PALETTE.BLUE))
    style_indic.set_radius(3)

    bar = lv.bar(lv.scr_act())
    bar.remove_style_all()
    bar.add_style(style_bg, 0)
    bar.add_style(style_indic, lv.PART.INDICATOR)

    bar.set_size(200, 20)
    bar.align_to(label2, lv.ALIGN.BOTTOM_MID, 0, 60)
    
try:
    sd = sdcard.SDCard(spi2, sd_cs)
    os.mount(sd, '/sd')
    ls = os.listdir('/sd')
    if 'main.py' in ls:
        init_lcd()
        print('update found on sd')
        if 'main.py' in os.listdir('/'):
            print('remove main.py from flash')
        print('copy main.py from sd to main.py on flash')
        file_size = os.stat('/sd/main.py')[6]
        cnt = 0
        with open('/sd/main.py', 'rb') as f:
            with open('/main.py', 'wb') as g:
                while g.write(f.read(1024)) == 1024:
                    cnt += 1
                    bar.set_value(int((cnt * 102400) / file_size), lv.ANIM.ON)
                    pass
        bar.set_value(100, lv.ANIM.ON)
        label1.set_text('remove sd card')
        label2.set_text('and restart')
        label2.align_to(label1, lv.ALIGN.BOTTOM_MID, 0, 50)
        print('done')
        while True:
            pass
except Exception as e:
    print("sd not found")
    print_exception(e)

ls = os.listdir('/')
if 'sd' in ls:
    os.umount('/sd')
if 'update.json' in ls:
    try:
        print('update info file found')
        import json
        with open('/update.json') as f:
            update_info = json.load(f)
        if f'main_{update_info["new_version"]}.py' in ls:
            init_lcd()
            print('update file found')
            if 'main.py' in ls:
                os.rename('/main.py', f'/main_{update_info["old_version"]}.py')
                print(f'renamed main.py to main_{update_info["old_version"]}.py')
            os.rename(f'/main_{update_info["new_version"]}.py', '/main.py')
            print(f'renamed main_{update_info["new_version"]}.py to main.py')
            bar.set_value(100, lv.ANIM.ON)
            sleep(1)
        else:
            print('update file not found')
        try:
            os.remove('/update.json')
        except:
            print('cannot remove update_info.json')
        machine.reset()
    except Exception as e:
        print_exception(e)