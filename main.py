import json
from sys import print_exception
import _thread
from MicroWebSrv2 import *
from time import sleep, sleep_ms, time
from max31865 import MAX31865
import machine
import SIM800L
import btree
from micropython import const
from SDI12 import SDI12
import sdcard
import ubinascii
import mpyaes
from network import WLAN, AP_IF
import lvgl as lv
from imagetools import get_png_info, open_png
from ili9XXX import ili9341
import espidf as esp
from umodbus.modbus import ModbusRTU
import os


Red='\033[0;31m'
Green='\033[0;32m'
Yellow='\033[0;33m'
Magenta='\033[0;35m'
Cyan='\033[0;36m'
White='\033[0;37m'

lv_red   = lv.color_make(255, 0, 0)
lv_green = lv.color_make(0, 255, 0)
lv_white = lv.color_make(255, 255, 255)
lv_yellow= lv.color_make(255, 255, 0)

lv.log_register_print_cb(print)
disp = ili9341(mosi=11, miso=13, clk=12, dc=9, rst=34, backlight=10, backlight_on=1, mhz=40, factor=32, hybrid=True, spihost=esp.VSPI_HOST, double_buffer=True)

_firmware_version = 0.3
_write_percip_interval = const(300)  # s
_prcip_update_interval = const(30)   # s
_sdi12_update_interval = const(30)   # s
_pt100_update_interval = const(30)   # s
_rs485_update_interval = const(30)   # s
_check_update_interval = const(3600) # s
_ais_update_interval   = const(30)   # s
_sms_check_interval    = const(300)  # s
_get_time_interval     = const(86400)# s

_sim800_tx = const(18)
_sim800_rx = const(17)
_sim800_en = const(38)
_sim800_pw = const(2)
_rs485_tx  = const(36)
_rs485_rx  = const(35)
_rs485_baud = 9600

ap = WLAN(AP_IF)
ap.config(max_clients=1)
ap.active(False)

rtc = machine.RTC()

led = machine.PWM(machine.Pin(37))
led.freq(1)
led.duty(102)

sdi12 = SDI12(0, 5)

percip   = machine.Pin( 4, machine.Pin.IN, machine.Pin.PULL_UP)
next_btn = machine.Pin(21, machine.Pin.IN, machine.Pin.PULL_UP)
prev_btn = machine.Pin(33, machine.Pin.IN, machine.Pin.PULL_UP)

spi2 = machine.SPI(2, baudrate=1320000, sck=machine.Pin(41), mosi=machine.Pin(40), miso=machine.Pin(42), phase=0)
sd_cs = machine.Pin(39, machine.Pin.OUT)
pt_cs = machine.Signal(sd_cs, invert=True)
pt = MAX31865(spi2, pt_cs, ref_resistor=470.0, wires=4)

AIs = {'a1': machine.ADC(machine.Pin(8),  atten=machine.ADC.ATTN_11DB),
       'a2': machine.ADC(machine.Pin(7),  atten=machine.ADC.ATTN_11DB),
       'a3': machine.ADC(machine.Pin(6),  atten=machine.ADC.ATTN_11DB),
       'c1': machine.ADC(machine.Pin(14), atten=machine.ADC.ATTN_11DB),
       'c2': machine.ADC(machine.Pin(3),  atten=machine.ADC.ATTN_11DB)}
bat = machine.ADC(machine.Pin(1), atten=machine.ADC.ATTN_11DB)
uart = machine.UART(1, tx=_sim800_tx, rx=_sim800_rx, baudrate=115200, rxbuf=1536)

boot = machine.Pin(0, machine.Pin.IN)
if boot() == 0:
    wdt = None
else:
    wdt = machine.WDT(timeout=15000)

def feed_wdt():
    if wdt is not None:
        wdt.feed( )
        
feed_wdt()
modem = SIM800L.Modem(uart, _sim800_pw, MODEM_POWER_ON_PIN=_sim800_en, log_level='DEBUG', wdt=wdt)

modbus = ModbusRTU(0, uart, ctrl_pin=45)


thread_lock = _thread.allocate_lock()
sdi_lock    = _thread.allocate_lock()
def print_colored(text, color=Red):
    print(f'{color}{text}{White}')

def print_reset_cause():
    code = machine.reset_cause()
    print_colored('reset reason: ')
    if code == machine.PWRON_RESET:
        print_colored('Power ON')
    elif code == machine.HARD_RESET:
        print_colored('Hard reset')
    elif code == machine.WDT_RESET:
        print_colored('Watchdog rset')
    elif code == machine.DEEPSLEEP_RESET:
        print_colored('Deep sleep reset')
    elif code == machine.SOFT_RESET:
        print_colored('Soft reset')
    else:
        print_colored('Unknown')

def roundup(num_to_round) -> int:
    rm = num_to_round % _write_percip_interval
    if rm == 0:
        return num_to_round
    return num_to_round + _write_percip_interval - rm

def check_config(config):
    keys = ['device_id', 'sms', 'gprs', 'log', 'enc', 'sdi12', 'rs485', 'sensor_list', 'sensors']
    sensors = ['pt', 'a1', 'a2', 'a3', 'c1', 'c2', 'ra', 'ra_1', 'ra_12', 'rs_1',
               's1', 's2', 's3', 's4', 's5', 's6', 's7', 's8', 's9']
    properties = ['en', 'disp_name', 'unit', 'a', 'b', 'sms_fun', 'sms_raw', 'sms_ord', 'high_th', 'low_th']
    bauds = [1200, 2400, 4800, 9600, 19200, 28800, 38400, 57600, 76800, 115200, 153600, 921600]
    for key in keys:
        if key not in config:
            return False, f'{key} not in config'
        
    if 'phone_1' not in config['sms']:
        return False, 'phone_1 not in sms'
    if 'phone_2' not in config['sms']:
        return False, 'phone_2 not in sms'
    if 'interval' not in config['sms']:
        return False, 'interval not in sms'
    try:
        int(config['sms']['interval'])
    except:
        return False, 'invalid sms interval'
    
    if 'apn' not in config['gprs']:
        return False, 'apn not in gprs'
    if 'server' not in config['gprs']:
        return False, 'server not in gprs'
    if 'interval' not in config['gprs']:
        return False, 'interval not in gprs'
    try:
        int(config['gprs']['interval'])
    except:
        return False, 'invalid gprs interval'
    
    if 'interval' not in config['log']:
        return False, 'interval not in log'
    try:
        int(config['log']['interval'])
    except:
        return False, 'invalid log interval'
    
    if 'key' not in config['enc']:
        return False, 'key not in enc'
    
    if 'en' not in config['sdi12']:
        return False, 'en not in sdi12'
    if 'addr' not in config['sdi12']:
        return False, 'addr not in sdi12'
    if config['sdi12']['en'] not in [0,1]:
        return False, 'invalid sdi12 en'
    if len(config['sdi12']['addr']) != 1 and config['sdi12']['addr'] not in '0123456789':
        return False, 'invalid sdi12 addr'
    
    if 'en' not in config['rs485']:
        return False, 'en not in rs485'
    if 'addr' not in config['rs485']:
        return False, 'addr not in rs485'
    if 'baud' not in config['rs485']:
        return False, 'baud not in rs485'
    if config['rs485']['en'] not in [0,1]:
        return False, 'invalid rs485 en'
    if config['rs485']['addr'] < 0:
        return False, 'invalid rs485 addr'
    if config['rs485']['baud'] not in bauds:
        return False, 'invalid rs485 baudrate'
        
    for sensor in sensors:
        if sensor not in config['sensors']:
            return False, f'{sensor} not in sensors'
        for p in properties:
            if p not in config['sensors'][sensor]:
                return False, f'{p}  not in {sensor}'
        if config['sensors'][sensor]['en'] not in [0, 1]:
            return False, f'invalid {sensor} en'
    
    return True, 'config saved successfully.'

def save_config(config:dict) -> int:
    try:
        with open('/config.json', 'w') as f:
            json.dump(config, f)
            return 0
    except:
        print_colored("Failed to save config on flash")
        return -1

def load_config() -> dict:
    try:
        with open('/config.json') as f:
            config = json.load(f)
            return config
    except:
        return {}
    
def delayed_restart():
    sleep(1)
    thread_lock.acquire()
    machine.reset()
    
app = MicroWebSrv2()
app.NotFoundURL = '/'
app.SetEmbeddedConfig()

@WebRoute(GET, '/')
def index(microWebSrv2, request):
    request.Response.ReturnFile('index.html')

@WebRoute(POST, '/')
def save(microWebSrv2, request):
    data = request.GetPostedJSONObject()
    print(data)
    config = load_config()
    try:
        config['device_id'] = data['device_id']
        config['gprs']['server'] = data['gprs_server']
        config['gprs']['apn'] = data['gprs_apn']
        config['gprs']['interval'] = int(data['gprs_interval'])
        
        config['sms']['phone_1'] = data['phone_1']
        config['sms']['phone_2'] = data['phone_2']
        config['sms']['interval'] = int(data['sms_interval'])
        
        config['log']['interval'] = int(data['log_interval'])
        
        config['enc']['key'] = data['enc_key']
        
        config['sdi12']['en'] = 1 if 'sdi12_en' in data else 0
        config['sdi12']['addr'] = data['sdi12_addr'] if 'sdi12_addr' in data else ''
        
        config['rs485']['en'] = 1 if 'rs485_en' in data else 0
        config['rs485']['addr'] = int(data['rs485_addr']) if 'rs485_addr' in data else 1
        config['rs485']['baud'] = int(data['rs485_baud']) if 'rs485_baud' in data else 9600
        
        for sensor in config['sensor_list']:
            if f'{sensor}_en' in data:
                config['sensors'][sensor]['en'] = 1
                config['sensors'][sensor]['disp_name'] = data[f'{sensor}_disp_name']
                config['sensors'][sensor]['unit'] = data[f'{sensor}_unit']
                config['sensors'][sensor]['a'] = round(float(data[f'{sensor}_a']), 2)
                config['sensors'][sensor]['b'] = round(float(data[f'{sensor}_b']), 2)
                if data[f'{sensor}_sms'] == '0':
                    config['sensors'][sensor]['sms_fun'] = 0
                    config['sensors'][sensor]['sms_raw'] = 0
                    config['sensors'][sensor]['sms_ord'] = 0
                elif data[f'{sensor}_sms'] == '1':
                    config['sensors'][sensor]['sms_fun'] = 1
                    config['sensors'][sensor]['sms_raw'] = 0
                    config['sensors'][sensor]['sms_ord'] = int(data[f'{sensor}_sms_order'])
                elif data[f'{sensor}_sms'] == '2':
                    config['sensors'][sensor]['sms_fun'] = 1
                    config['sensors'][sensor]['sms_raw'] = 1
                    config['sensors'][sensor]['sms_ord'] = int(data[f'{sensor}_sms_order'])
                    
                if f'{sensor}_high_th' in data:
                    try:
                        config['sensors'][sensor]['high_th'] = round(float(data[f'{sensor}_high_th']), 2)
                    except:
                        config['sensors'][sensor]['high_th'] = None
                else:
                    config['sensors'][sensor]['high_th'] = None
                    
                if f'{sensor}_low_th' in data:
                    try:
                        config['sensors'][sensor]['low_th'] = round(float(data[f'{sensor}_low_th']), 2)
                    except:
                        config['sensors'][sensor]['low_th'] = None
                else:
                    config['sensors'][sensor]['low_th'] = None
            else:
                config['sensors'][sensor]['en'] = 0
        
        result, msg = check_config(config)
        if result:
            save_config(config)

    except Exception as e:
        print_colored("Exception from handle form:", Magenta)
        print_exception(e)
        msg = 'Failed to save config'
        result = False

    request.Response.ReturnOkJSON({'msg': msg, 'result': result})
    

@WebRoute(GET, '/config.json')
def config(microWebSrv2, request):
    request.Response.ReturnFile('config.json')
    
@WebRoute(GET, '/loading.gif')
def loading(microWebSrv2, request):
    request.Response.ReturnFile('loading.gif')
    
@WebRoute(GET, '/scan_sdi')
def scan_sdi(microWebSrv2, request):
    sdi_lock.acquire()
    result  = sdi12.scan()
    sdi_lock.release()
    if result:
        request.Response.ReturnOkJSON({'result': True, 'addr': result[0]})
    else:
        request.Response.ReturnOkJSON({'result': False})
    
@WebRoute(POST, '/change_sdi')
def change_sdi(microWebSrv2, request):
    data = request.GetPostedJSONObject()
    sdi_lock.acquire()
    result = sdi12.scan()
    if result:
        old_addr = result[0]
        result = sdi12.change_address(old_addr, data['addr'])
        if result:
            request.Response.ReturnOkJSON({'result': True, 'msg': f'sensor address changed to {data["addr"]}'})
        else:
            request.Response.ReturnOkJSON({'result': False, 'msg': 'Failed to change address'})
    else:
        request.Response.ReturnOkJSON({'result': False, 'msg': 'Sensor not found'})
    sdi_lock.release()

@WebRoute(GET, '/restart')
def restart(microWebSrv2, request):
    _thread.start_new_thread(delayed_restart, ())
    request.Response.ReturnOkJSON({'msg': 'Restarting device...', 'result': True})

class Job:
    def __init__(self, name, func, args):
        self.name = name
        self.func = func
        self.args = args
        
    def __eq__(self, other: Job):
        return self.name == other.name and self.args == other.args
    
    def __str__(self):
        return self.name
    
    def __repr__(self):
        return self.name

class App:
    def __init__(self):
        self.spi_lock = False
        self.uart_lock = False
        self.data = {}
        self.config = {}
        self.data['firmware_version'] = _firmware_version
        self.uart_tx = 18
        self.uart_rx = 17
        
        self.last_prcip_write  = float('-inf')
        self.last_prcip_update = float('-inf')
        self.last_sdi12_update = float('-inf')
        self.last_pt100_update = float('-inf')
        self.last_rs485_update = float('-inf')
        self.last_update_check = float('-inf')
        self.last_ais_update   = float('-inf')
        self.last_sms_check    = float('-inf')
        self.last_data_post    = float('-inf')
        self.last_get_time     = float('-inf')
        self.last_data_sms     = float('-inf')
        self.last_sd_log       = float('-inf')
        
        self.prcip_update_running = False
        self.sdi12_update_running = False
        self.pt100_update_running = False
        self.rs485_update_running = False
        self.loc_request_sms_sent = False
        self.ais_update_running   = False
        self.sd_log_running       = False
        self.server_running       = False
        
        self.next_ready = False
        self.prev_ready = False
        
        self.sms_time_set = False
        self.time_set = False
        self.sim800_jobs = []
        self.sensor_jobs = []
        self.sensors_handler1_running = False
        self.sensors_handler2_running = False
        
        self.decoder = lv.img.decoder_create()
        self.decoder.info_cb = get_png_info
        self.decoder.open_cb = open_png
        
    def init_display(self):
        print_colored('Initializing display...')
        self.scr = lv.scr_act()
        self.scr.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
        
        self.time_label = lv.label(self.scr)
        self.time_label.set_pos(3, 3)
        self.time_label.set_text("")
        self.time_label.set_style_text_font(lv.font_unscii_8, 0)
        
        self.tabview = lv.tabview(self.scr, lv.DIR.TOP, 25)
        self.tabview.set_pos(0, 25)
        self.tabview.set_size(240, 255)
        self.cur_tab = 0
        
        self.lcd_objs = {}
        
        self.sdi_tab = self.tabview.add_tab("sdi12")
        self.sdi_tab.set_style_pad_left(5, 0)
        self.sdi_tab.set_style_pad_right(5, 0)
        self.sdi_tab.set_style_pad_top(3, 0)
        self.sdi_tab.set_style_pad_bottom(0, 0)
        
        self.ai_tab = self.tabview.add_tab("AI")
        self.ai_tab.set_style_pad_left(5, 0)
        self.ai_tab.set_style_pad_right(5, 0)
        self.ai_tab.set_style_pad_top(3, 0)
        self.ai_tab.set_style_pad_bottom(0, 0)
        
        self.di_tab = self.tabview.add_tab("DI")
        self.di_tab.set_style_pad_left(5, 0)
        self.di_tab.set_style_pad_right(5, 0)
        self.di_tab.set_style_pad_top(3, 0)
        self.di_tab.set_style_pad_bottom(0, 0)
        
        self.info_tab  = self.tabview.add_tab("info")
        label = lv.label(self.info_tab)
        label.set_text(f"device id: {self.config['device_id']}")
        label.set_pos(0, 0)
        label.set_size(200, 40)
        label.set_style_text_font(lv.font_montserrat_18, 0)
        label = lv.label(self.info_tab)
        label.set_text(f"firmware version: {_firmware_version}")
        label.set_pos(0, 40)
        label.set_size(200, 40)
        label.set_style_text_font(lv.font_montserrat_18, 0)
        
        label = lv.label(self.info_tab)
        label.set_text(f"lat:")
        label.set_pos(0, 80)
        label.set_size(50, 40)
        label.set_style_text_font(lv.font_montserrat_18, 0)
        
        label = lv.label(self.info_tab)
        label.set_text("")
        label.set_pos(55, 80)
        label.set_size(150, 40)
        label.set_style_text_font(lv.font_montserrat_18, 0)
        self.lcd_objs['lat'] = label
        
        label = lv.label(self.info_tab)
        label.set_text(f"lon:")
        label.set_pos(0, 120)
        label.set_size(50, 40)
        label.set_style_text_font(lv.font_montserrat_18, 0)
        
        label = lv.label(self.info_tab)
        label.set_text("")
        label.set_pos(55, 120)
        label.set_size(150, 40)
        label.set_style_text_font(lv.font_montserrat_18, 0)
        self.lcd_objs['lon'] = label
        
        label = lv.label(self.info_tab)
        label.set_text(f"radius:")
        label.set_pos(0, 160)
        label.set_size(60, 40)
        label.set_style_text_font(lv.font_montserrat_18, 0)
        
        label = lv.label(self.info_tab)
        label.set_text("")
        label.set_pos(65, 160)
        label.set_size(140, 40)
        label.set_style_text_font(lv.font_montserrat_18, 0)
        self.lcd_objs['rad'] = label
        
        label = lv.label(self.scr)
        label.set_text("Initializing...")
        label.set_pos(5, 280)
        label.set_size(230, 20)
        label.set_long_mode(lv.label.LONG.CLIP)
        label.set_style_text_color(lv_yellow, 0)
        self.lcd_objs['status'] = label
        
        self.wifi_icon = lv.img(main_app.scr)
        self.wifi_icon.set_size(20, 20)
        self.wifi_icon.set_pos(220, 0)
        self.wifi_icon.set_src(lv.SYMBOL.CLOSE)
        
        self.bat_label = lv.label(main_app.scr)
        self.bat_label.set_text("")
        self.bat_label.set_pos(165, 0)
        self.bat_label.set_style_text_font(lv.font_montserrat_12, 0)
        
        label = lv.label(self.scr)
        label.set_text("sdi12")
        label.set_style_text_font(lv.font_montserrat_14, 0)
        label.set_pos(5, 295)
        label.set_size(34, 25)
        self.lcd_objs['sdi_th'] = label
        
        label = lv.label(self.scr)
        label.set_text("pt100")
        label.set_style_text_font(lv.font_montserrat_14, 0)
        label.set_pos(45, 295)
        label.set_size(39, 25)
        self.lcd_objs['pt_th'] = label
        
        label = lv.label(self.scr)
        label.set_text("AI")
        label.set_style_text_font(lv.font_montserrat_14, 0)
        label.set_pos(90, 295)
        label.set_size(14, 25)
        self.lcd_objs['ai_th'] = label
        
        label = lv.label(self.scr)
        label.set_text("percip")
        label.set_style_text_font(lv.font_montserrat_14, 0)
        label.set_pos(110, 295)
        label.set_size(47, 25)
        self.lcd_objs['ra_th'] = label
        
        label = lv.label(self.scr)
        label.set_text("rs485")
        label.set_style_text_font(lv.font_montserrat_14, 0)
        label.set_pos(165, 295)
        label.set_size(40, 25)
        self.lcd_objs['rs_th'] = label
        
        label = lv.label(self.scr)
        label.set_text("log")
        label.set_style_text_font(lv.font_montserrat_14, 0)
        label.set_pos(210, 295)
        label.set_size(23, 25)
        if not self.sd_available:
            label.set_style_text_color(lv_red, 0)
        self.lcd_objs['sd_th'] = label
        
        self.time_timer = lv.timer_create(self.update_time, 1000, None)
        self.btn_timer = lv.timer_create(self.scan_btns, 200, None)
#         self.btn_timer = machine.Timer(2)
#         self.btn_timer.init(mode=machine.Timer.PERIODIC, period=200, callback=self.scan_btns)
        
    def update_time(self, timer):
        try:
            tm = rtc.datetime()
            self.time_label.set_text(f"{tm[0]:04d}-{tm[1]:02d}-{tm[2]:02d} {tm[4]:02d}:{tm[5]:02d}:{tm[6]:02d}")
        except Exception as e:
            print_exception(e)
    
    def sim800_handler(self):
        if self.sim800_jobs:
            print_colored(f'jobs: {self.sim800_jobs}', Green)
            while self.uart_lock:
                sleep_ms(100)
            self.uart_lock = True
            self.switch_uart_to('sim800')
            self.lcd_objs['status'].set_text("Initializing modem...")
            if not self.init_modem():
                self.wifi_icon.set_src(lv.SYMBOL.CLOSE)
                self.lcd_objs['status'].ins_text(lv.LABEL_POS.LAST, "Failed")
                self.last_get_time = time()
                self.get_time_running = False
                self.uart_lock = False
                self.sim_handler_running = False
                return
            self.lcd_objs['status'].ins_text(lv.LABEL_POS.LAST, "done")
            if uart.any():
                uart.read()
            csq = modem.get_signal_strength()
            if csq >= 20:
                with open("/icons/4.png", 'rb') as f:
                    icon = f.read()
                    img_data = lv.img_dsc_t({'data_size':len(icon), 'data':icon})
            elif csq >= 15:
                with open("/icons/3.png", 'rb') as f:
                    icon = f.read()
                    img_data = lv.img_dsc_t({'data_size':len(icon), 'data':icon})
            elif csq >= 10:
                with open("/icons/2.png", 'rb') as f:
                    icon = f.read()
                    img_data = lv.img_dsc_t({'data_size':len(icon), 'data':icon})
            else:
                with open("/icons/1.png", 'rb') as f:
                    icon = f.read()
                    img_data = lv.img_dsc_t({'data_size':len(icon), 'data':icon})
            self.wifi_icon.set_src(img_data)
            self.lcd_objs['status'].set_text(f'{self.sim800_jobs[0].name}...')
            print_colored(f'running {self.sim800_jobs[0].name} job', Green)
            try:
                self.sim800_jobs[0].func(*self.sim800_jobs[0].args)
                self.lcd_objs['status'].ins_text(lv.LABEL_POS.LAST, "done")
            except Exception as e:
                print_colored("Exception from sim800 handle:", Cyan)
                self.lcd_objs['status'].ins_text(lv.LABEL_POS.LAST, "Failed")
                print_exception(e)
            finally:
                self.uart_lock = False
                thread_lock.acquire()
                self.sim800_jobs.pop(0)
                thread_lock.release()

    def init_sensors(self):
        sdi_th = False
        if self.config['sdi12']['en']:
            idx = 0
            for i in range(9):
                sensor = f's{i+1}'
                if self.config['sensors'][sensor]['en']:
                    sdi_th = True
                    self.data[f"s{i+1}"] = {"raw": None, "scaled": None, "warning": None}
                    label = lv.label(self.sdi_tab)
                    label.set_text(f"{self.config['sensors'][sensor]['disp_name']}:")
                    label.set_pos(0, idx * 25)
                    label.set_size(95, 25)
                    label.set_style_text_font(lv.font_montserrat_18, 0)
                    label.set_long_mode(lv.label.LONG.DOT)
                    label = lv.label(self.sdi_tab)
                    label.set_text('')
                    label.set_pos(100, idx * 25)
                    label.set_size(100, 25)
                    label.set_style_text_font(lv.font_montserrat_18, 0)
                    label.set_long_mode(lv.label.LONG.SCROLL_CIRCULAR)
                    self.lcd_objs[sensor] = label
                    label = lv.label(self.sdi_tab)
                    label.set_pos(200, idx * 25 + 2)
                    label.set_size(30, 25)
                    label.set_style_text_font(lv.font_montserrat_12, 0)
                    label.set_text(self.config['sensors'][sensor]['unit'])
                    idx += 1
            if not sdi_th:
                self.lcd_objs['sdi_th'].set_style_text_color(lv_red, 0)
        else:
            self.lcd_objs['sdi_th'].set_style_text_color(lv_red, 0)
        
        idx = 0
        sensor = 'pt'
        if self.config['sensors'][sensor]['en']:
            self.data[sensor] = {"raw": None, "scaled": None, "warning": None}
            label = lv.label(self.ai_tab)
            label.set_text(f"{self.config['sensors'][sensor]['disp_name']}:")
            label.set_pos(0, idx * 35)
            label.set_size(95, 35)
            label.set_style_text_font(lv.font_montserrat_18, 0)
            label.set_long_mode(lv.label.LONG.DOT)
            label = lv.label(self.ai_tab)
            label.set_text('')
            label.set_pos(100, idx * 35)
            label.set_size(100, 35)
            label.set_style_text_font(lv.font_montserrat_18, 0)
            label.set_long_mode(lv.label.LONG.SCROLL_CIRCULAR)
            self.lcd_objs[sensor] = label
            label = lv.label(self.ai_tab)
            label.set_pos(200, idx * 35 + 2)
            label.set_size(30, 35)
            label.set_style_text_font(lv.font_montserrat_12, 0)
            label.set_text(self.config['sensors'][sensor]['unit'])
            idx += 1
        else:
            self.lcd_objs['pt_th'].set_style_text_color(lv_red, 0)
        
        ai_th = False
        for sensor in ['a1', 'a2', 'a3', 'c1', 'c2']:
            if self.config['sensors'][sensor]['en']:
                ai_th = True
                self.data[sensor] = {"raw": None, "scaled": None, "warning": None}
                label = lv.label(self.ai_tab)
                label.set_text(f"{self.config['sensors'][sensor]['disp_name']}:")
                label.set_pos(0, idx * 35)
                label.set_size(95, 35)
                label.set_style_text_font(lv.font_montserrat_18, 0)
                label.set_long_mode(lv.label.LONG.DOT)
                label = lv.label(self.ai_tab)
                label.set_text('')
                label.set_pos(100, idx * 35)
                label.set_size(100, 35)
                label.set_style_text_font(lv.font_montserrat_18, 0)
                label.set_long_mode(lv.label.LONG.SCROLL_CIRCULAR)
                self.lcd_objs[sensor] = label
                label = lv.label(self.ai_tab)
                label.set_pos(200, idx * 35 + 2)
                label.set_size(30, 35)
                label.set_style_text_font(lv.font_montserrat_12, 0)
                label.set_text(self.config['sensors'][sensor]['unit'])
                idx += 1
        if not ai_th:
            self.lcd_objs['ai_th'].set_style_text_color(lv_red, 0)
        
        idx = 0
        if self.config['sensors']['ra']['en']:
            self.percip_cnt = 0
            self.percip_ready = False
            for sensor in ['ra', 'ra_1', 'ra_12']:
                self.data[sensor] = {"raw": 0, "scaled": 0, "warning": 0}
                label = lv.label(self.di_tab)
                label.set_text(f"{self.config['sensors'][sensor]['disp_name']}:")
                label.set_pos(0, idx * 35)
                label.set_size(110, 35)
                label.set_style_text_font(lv.font_montserrat_18, 0)
                label.set_long_mode(lv.label.LONG.DOT)
                label = lv.label(self.di_tab)
                label.set_text('')
                label.set_pos(115, idx * 35)
                label.set_size(85, 35)
                label.set_style_text_font(lv.font_montserrat_18, 0)
                label.set_long_mode(lv.label.LONG.SCROLL_CIRCULAR)
                self.lcd_objs[sensor] = label
                label = lv.label(self.di_tab)
                label.set_pos(200, idx * 35 + 2)
                label.set_size(30, 35)
                label.set_style_text_font(lv.font_montserrat_12, 0)
                label.set_text(self.config['sensors'][sensor]['unit'])
                idx += 1
        else:
            self.lcd_objs['ra_th'].set_style_text_color(lv_red, 0)
        
        if self.config['rs485']['en']:
            modbus._addr_list = [self.config['rs485']['addr']]
            _rs485_baud = self.config['rs485']['baud']
            
            for sensor in ['rs_1', 'rs_2']:
                self.data[sensor] = {"raw": 0, "scaled": 0, "warning": 0}
                label = lv.label(self.di_tab)
                label.set_text(f"{self.config['sensors'][sensor]['disp_name']}:")
                label.set_pos(0, idx * 35)
                label.set_size(110, 35)
                label.set_style_text_font(lv.font_montserrat_18, 0)
                label.set_long_mode(lv.label.LONG.DOT)
                label = lv.label(self.di_tab)
                label.set_text('')
                label.set_pos(115, idx * 35)
                label.set_size(85, 35)
                label.set_style_text_font(lv.font_montserrat_18, 0)
                label.set_long_mode(lv.label.LONG.SCROLL_CIRCULAR)
                self.lcd_objs[sensor] = label
                label = lv.label(self.di_tab)
                label.set_pos(200, idx * 35 + 2)
                label.set_size(30, 35)
                label.set_style_text_font(lv.font_montserrat_12, 0)
                label.set_text(self.config['sensors'][sensor]['unit'])
                idx += 1
        else:
            self.lcd_objs['rs_th'].set_style_text_color(lv_red, 0)

        self.pin_timer = machine.Timer(3)
        self.pin_timer.init(mode=machine.Timer.PERIODIC, period=100, callback=self.scan_pins)
        print_colored("started scan pins timer")
            
    def scan_pins(self, t):
        if percip.value():
            self.percip_ready = True
        
        if not percip.value() and self.percip_ready:
            self.percip_ready = False
            self.percip_cnt += 1
              
    def scan_btns(self, t):
        if next_btn.value():
            self.next_ready = True
        
        if prev_btn.value():
            self.prev_ready = True

        if not next_btn.value() and self.next_ready:
            self.next_ready = False
            self.go_to_next_tab()
            
        if not prev_btn.value() and self.prev_ready:
            self.prev_ready = False
            self.go_to_previous_tab()
    
    def ap_and_srv_ctrl(self, start):
        if start and not app.IsRunning:
            ap.active(True)
            app.StartManaged()
        else:
            app.Stop()
            ap.active(False)
    
    def go_to_next_tab(self):
        self.cur_tab += 1
        if self.cur_tab == 4:
            self.cur_tab = 0
        self.tabview.set_act(self.cur_tab, 0)
        _thread.start_new_thread(self.ap_and_srv_ctrl, (self.cur_tab == 3,))
        
    def go_to_previous_tab(self):
        self.cur_tab -= 1
        if self.cur_tab == -1:
            self.cur_tab = 3
        self.tabview.set_act(self.cur_tab, 0)
        _thread.start_new_thread(self.ap_and_srv_ctrl, (self.cur_tab == 3,))
    
    def create_old_percip_record(self):
        try:
            tm = roundup(time())
            tm -= _write_percip_interval
            tm = tm.to_bytes(4, 'big')
            thread_lock.acquire()
            if tm not in self.db:
                print_colored(f'update percip db: {int.from_bytes(tm, "big")} -> {self.percip_tot}', Cyan)
                self.db[tm] = self.percip_tot.to_bytes(4, 'big')
                self.db.flush()
                self.db_file.flush()
        except Exception as e:
            print_colored("Exception from create_old_percip_record:", Cyan)
            print_exception(e)
        finally:
            if thread_lock.locked():
                thread_lock.release()
            
    
    def init_percip_db(self):
        try:
            self.db_file = open("percip.db", "r+b")
        except OSError:
            self.db_file = open("percip.db", "w+b")
            
        self.db = btree.open(self.db_file)
        self.percip_cnt = 0
        self.percip_cur = 0
        self.percip_tot = 0
        keys = list(self.db)
        
        if keys:
            self.percip_tot = int.from_bytes(self.db[keys[-1]], 'big')
        else:
            print_colored(f'update percip db: 0 -> {self.percip_tot}', Cyan)
            self.db[bytes(4)] = self.percip_tot.to_bytes(4, 'big')
            self.db.flush()
            self.db_file.flush()
        
    def update_percip(self, *args):
        self.prcip_update_running = True
        self.lcd_objs['ra_th'].set_style_text_color(lv_green, 0)
        try:
            a = self.config["sensors"]["ra"]["a"]
            b = self.config["sensors"]["ra"]["b"]

            tm = roundup(time())
            thread_lock.acquire()
            
            keys = list(self.db)
            if len(keys) >= 300:
                print_colored(f"dblen: {len(keys)} remove 10 keys from db", Cyan)
                for i in range(10):
                    del self.db[keys[i]]
                self.db.flush()
                self.db_file.flush()
            
            self.percip_tot += self.percip_cnt
            self.percip_cur += self.percip_cnt

            if self.percip_cnt != 0 or tm.to_bytes(4, 'big') not in self.db:
                print_colored(f'update percip db: {tm} -> {self.percip_tot}', Cyan) 
                self.db[tm.to_bytes(4, 'big')] = self.percip_tot.to_bytes(4, 'big')
                self.db.flush()
                self.db_file.flush()
            self.percip_cnt = 0
            tm_1h = tm - 3600
            
            while tm > tm_1h:
                b_tm = tm_1h.to_bytes(4, 'big')
                if b_tm in self.db:
                    pr_1h = self.percip_tot - int.from_bytes(self.db[b_tm], 'big')
                    break
                tm_1h += _write_percip_interval
            else:
                pr_1h = self.percip_cur
                
            tm_12 = tm - 43200
            while tm > tm_12:
                b_tm = tm_12.to_bytes(4, 'big')
                if b_tm in self.db:
                    pr_12 = self.percip_tot - int.from_bytes(self.db[b_tm], 'big')
                    break
                tm_12 += _write_percip_interval
            else:
                pr_12 = self.percip_cur
            
            print_colored(f'percip -> t: {self.percip_tot} 1h: {pr_1h} 12h: {pr_12}', Cyan)
            self.data["ra"]["raw"] = round(self.percip_tot, 2)
            self.data["ra"]["scaled"] = round(a * self.percip_tot + b, 2)
            self.data["ra_1"]["raw"] = round(pr_1h, 2)
            self.data["ra_1"]["scaled"] = round(a * pr_1h + b, 2)
            self.data["ra_12"]["raw"] = round(pr_12, 2)
            self.data["ra_12"]["scaled"] = round(a * pr_12 + b, 2)
            
            self.lcd_objs["ra"].set_text(f'{self.data["ra"]["scaled"]}')
            self.lcd_objs["ra_1"].set_text(f'{self.data["ra_1"]["scaled"]}')
            self.lcd_objs["ra_12"].set_text(f'{self.data["ra_12"]["scaled"]}')
            
            if self.config["sensors"]["ra"]["high_th"] and self.data["ra"]["scaled"] > float(self.config["sensors"]["ra"]["high_th"]):
                self.add_sim800_job('send_alarm_sms', 'ra', True)
                
            if self.config["sensors"]["ra_1"]["high_th"] and self.data["ra_1"]["scaled"] > float(self.config["sensors"]["ra_1"]["high_th"]):
                self.add_sim800_job('send_alarm_sms', 'ra_1', True)
                
            if self.config["sensors"]["ra_12"]["high_th"] and self.data["ra_12"]["scaled"] > float(self.config["sensors"]["ra_12"]["high_th"]):
                self.add_sim800_job('send_alarm_sms', 'ra_12', True)
            
            if self.config["sensors"]["ra"]["low_th"] and self.data["ra"]["scaled"] < float(self.config["sensors"]["ra"]["low_th"]):
                self.add_sim800_job('send_alarm_sms', 'ra', False)
                
            if self.config["sensors"]["ra_1"]["low_th"] and self.data["ra_1"]["scaled"] < float(self.config["sensors"]["ra_1"]["low_th"]):
                self.add_sim800_job('send_alarm_sms', 'ra_1', False)
                
            if self.config["sensors"]["ra_12"]["low_th"] and self.data["ra_12"]["scaled"] < float(self.config["sensors"]["ra_12"]["low_th"]):
                self.add_sim800_job('send_alarm_sms', 'ra_12', False)

        except Exception as e:
            print_colored("Exception from percip handle:", Cyan)
            print_exception(e)
        finally:
            self.last_prcip_update = time()
            self.prcip_update_running = False
            self.lcd_objs['ra_th'].set_style_text_color(lv_white, 0)
            if thread_lock.locked():
                thread_lock.release()
    
    def zero_db(self):
        thread_lock.acquire()
        keys = list(self.db)
        for key in keys:
            del self.db[key]
        self.percip_cnt = 0
        self.percip_tot = 0
        self.percip_cur = 0
        self.db.flush()
        self.db_file.flush()
        thread_lock.release()
    
    def update_sdi(self, *args):
        self.sdi12_update_running = True
        self.lcd_objs['sdi_th'].set_style_text_color(lv_green, 0)
        addr = str(self.config['sdi12']['addr'])
        retries = 0
        sdi_lock.acquire()
        while retries < 10:
            try:
                feed_wdt()
                result = sdi12.measure_data(addr, request_crc = True)
                print_colored(f'sdi {addr} data: {result}', Cyan)
                if result[0] > 0:
                    cnt, data = result
                    break
                sleep(1)
                retries += 1
            except:
                retries += 1
                sleep(1)
                continue
        else:
            cnt = 0
            data = []
        sdi_lock.release()
        feed_wdt()
        try:
            thread_lock.acquire()
            if data:
                for i in range(9):
                    sensor = f's{i+1}'
                    if self.config['sensors'][sensor]['en']:
                        a = float(self.config['sensors'][sensor]['a'])
                        b = float(self.config['sensors'][sensor]['b'])
                        self.data[sensor]['raw'] = round(data[i], 2)
                        self.data[sensor]['scaled'] = round(a * data[i] + b, 2)
                        self.data[sensor]['warning'] = 0
                        if self.config["sensors"][sensor]["high_th"] and self.data[sensor]["scaled"] > float(self.config["sensors"][sensor]["high_th"]):
                            self.add_sim800_job('send_alarm_sms', sensor, True)
                            self.data[sensor]['warning'] = 2
                        
                        if self.config["sensors"][sensor]["low_th"] and self.data[sensor]["scaled"] < float(self.config["sensors"][sensor]["low_th"]):
                            self.add_sim800_job('send_alarm_sms', sensor, False)
                            self.data[sensor]['warning'] = 3
                        self.lcd_objs[sensor].set_text(f"{self.data[sensor]['scaled']}")
            else:
                for i in range(9):
                    sensor = f's{i+1}'
                    if self.config['sensors'][sensor]['en']:
                        self.data[sensor]['raw'] = None
                        self.data[sensor]['scaled'] = None
                        self.data[sensor]['warning'] = 1
                        self.lcd_objs[sensor].set_text('NC')
        except Exception as e:
            print_colored(f"Exception from sdi12 update:", Cyan)
            print_exception(e)
        finally:
            self.last_sdi12_update = time()
            self.sdi12_update_running = False
            self.lcd_objs['sdi_th'].set_style_text_color(lv_white, 0)
            if thread_lock.locked():
                thread_lock.release()
                        
    def update_pt100(self, *args):
        self.pt100_update_running = True
        self.lcd_objs['pt_th'].set_style_text_color(lv_green, 0)
        try:
            a = self.config["sensors"]["pt"]["a"]
            b = self.config["sensors"]["pt"]["b"]

            while self.spi_lock:
                sleep_ms(100)
            self.spi_lock = True
            spi2.init(baudrate=5000000, phase=1)
            for _ in range(3):
                feed_wdt()
                try:
                    tmp = pt.temperature
                    if -100 < tmp < 100:
                        break
                except:
                    tmp = None
            else:
                tmp = None
            
            self.spi_lock = False
            thread_lock.acquire()
            if tmp is not None:
                print_colored(f'pt100: {tmp}', Cyan)
                self.data['pt']['raw'] = round(tmp, 2)
                self.data['pt']['scaled'] = round(a * tmp + b, 2)
                self.data['pt']['warning'] = 0
                
                if self.config["sensors"]['pt']["high_th"] and self.data['pt']["scaled"] > float(self.config["sensors"]['pt']["high_th"]):
                    self.add_sim800_job('send_alarm_sms', 'pt', True)
                    self.data['pt']['warning'] = 2
                    
                if self.config["sensors"]['pt']["low_th"] and self.data['pt']["scaled"] < float(self.config["sensors"]['pt']["low_th"]):
                    self.add_sim800_job('send_alarm_sms', 'pt', False)
                    self.data['pt']['warning'] = 3
                self.lcd_objs['pt'].set_text(f'{self.data["pt"]["scaled"]}')
            else:
                print_colored(f'pt100: NC', Cyan)
                self.data['pt']['raw'] = None
                self.data['pt']['scaled'] = None
                self.data['pt']['warning'] = 1
                self.lcd_objs['pt'].set_text('NC')

        except Exception as e:
            print_colored(f"Exception from pt100 handle:", Cyan)
            print_exception(e)
        finally:
            self.last_pt100_update = time()
            self.pt100_update_running = False
            self.lcd_objs['pt_th'].set_style_text_color(lv_white, 0)
            if thread_lock.locked():
                thread_lock.release()

    def update_ais(self, *args):
        self.ais_update_running = True
        ai_th = False
        try:
            for sensor in ['a1', 'a2', 'a3']:
                if sensor in self.config['sensors'] and self.config['sensors'][sensor]['en']:
                    self.lcd_objs['ai_th'].set_style_text_color(lv_green, 0)
                    ai_th = True
                    a = self.config["sensors"][sensor].get("a", 1.0)
                    b = self.config["sensors"][sensor].get("b", 0.0)
                    tmp = 0
                    for _ in range(10):
                        tmp += AIs[sensor].read_uv()
                        sleep_ms(10)
                    tmp /= 10
                    tmp = tmp * 0.000004636636 - (tmp * 0.000000022)
                    thread_lock.acquire()
                    self.data[sensor]['raw'] = round(tmp, 2)                
                    self.data[sensor]['scaled'] = round(a * tmp + b, 2)
                    self.data[sensor]['warning'] = 0
                    print_colored(f'{sensor} : {self.data[sensor]}', Cyan)
                    self.lcd_objs[sensor].set_text(f'{self.data[sensor]["scaled"]}')
                    if self.config["sensors"][sensor]["high_th"] and self.data[sensor]["scaled"] > float(self.config["sensors"][sensor]["high_th"]):
                        self.add_sim800_job('send_alarm_sms', sensor, True)
                        self.data[sensor]['warning'] = 2
                        
                    if self.config["sensors"][sensor]["low_th"] and self.data[sensor]["scaled"] < float(self.config["sensors"][sensor]["low_th"]):
                        self.add_sim800_job('send_alarm_sms', sensor, False)
                        self.data[sensor]['warning'] = 3
                    thread_lock.release()
            for sensor in ['c1', 'c2']:
                
                if sensor in self.config['sensors'] and self.config['sensors'][sensor]['en']:
                    a = self.config["sensors"][sensor].get("a", 1.0)
                    b = self.config["sensors"][sensor].get("b", 0.0)
                    tmp = 0
                    for _ in range(10):
                        tmp += AIs[sensor].read_uv()
                        sleep_ms(10)
                    tmp *= 0.00000144
                    thread_lock.acquire()
                    self.data[sensor]['raw'] = round(tmp, 2)                
                    self.data[sensor]['scaled'] = round(a * tmp + b, 2)
                    self.data[sensor]['warning'] = 0
                    print_colored(f'{sensor} : {self.data[sensor]}', Cyan)
                    self.lcd_objs[sensor].set_text(f'{self.data[sensor]["scaled"]}')
                    if self.config["sensors"][sensor]["high_th"] and self.data[sensor]["scaled"] > float(self.config["sensors"][sensor]["high_th"]):
                        self.add_sim800_job('send_alarm_sms', sensor, True)
                        self.data[sensor]['warning'] = 2
                    if self.config["sensors"][sensor]["low_th"] and self.data[sensor]["scaled"] < float(self.config["sensors"][sensor]["low_th"]):
                        self.add_sim800_job('send_alarm_sms', sensor, False)
                        self.data[sensor]['warning'] = 3
                    thread_lock.release()
        except Exception as e:
            print_colored(f"Exception from AIs handle:", Cyan)
            print_exception(e)
        finally:
            self.last_ais_update = time()
            self.ais_update_running = False
            if ai_th:
                self.lcd_objs['ai_th'].set_style_text_color(lv_white, 0)
            if thread_lock.locked():
                thread_lock.release()
    
    def update_rs485(self, *args):
        self.rs485_update_running = True
        self.lcd_objs['rs_th'].set_style_text_color(lv_green, 0)
        try:
            addr = self.config['rs485']['addr']
            while self.uart_lock:
                sleep_ms(100)
            self.uart_lock = True
            self.switch_uart_to('rs485')
            for _ in range(3):
                try:
                    feed_wdt()
                    rs_1, rs_2 = modbus._itf.read_holding_registers(addr, 1, 2)
                    break
                except:
                    rs_1, rs_2 = None, None
            else:
                rs_1, rs_2 = None, None
            self.uart_lock = False
            print_colored(f'rs485: {rs_1} {rs_2}', Cyan)
            
            thread_lock.acquire()
            if self.config["sensors"]["rs_1"]['en']:
                if rs_1 is not None:
                    a1 = self.config["sensors"]["rs_1"]["a"]
                    b1 = self.config["sensors"]["rs_1"]["b"]
                    self.data['rs_1']['raw'] = round(rs_1, 2)
                    self.data['rs_1']['scaled'] = round(a1 * rs_1 + b1, 2)
                    self.data['rs_1']['warning'] = 0
                    self.lcd_objs['rs_1'].set_text(f'{self.data["rs_1"]["scaled"]}')
                    if self.config["sensors"]['rs_1']["high_th"] and self.data['rs_1']["scaled"] > float(self.config["sensors"]['rs_1']["high_th"]):
                        self.add_sim800_job('send_alarm_sms', 'rs_1', True)
                        self.data['rs_1']['warning'] = 2
                        
                    if self.config["sensors"]['rs_1']["low_th"] and self.data['rs_1']["scaled"] < float(self.config["sensors"]['rs_1']["low_th"]):
                        self.add_sim800_job('send_alarm_sms', 'rs_1', False)
                        self.data['rs_1']['warning'] = 3
                else:
                    self.data['rs_1']['raw'] = None
                    self.data['rs_1']['scaled'] = None
                    self.data['rs_1']['warning'] = 1
                    self.lcd_objs['rs_1'].set_text('NC')
                    
            if self.config["sensors"]["rs_2"]['en']:
                if rs_2 is not None:
                    a2 = self.config["sensors"]["rs_2"]["a"]
                    b2 = self.config["sensors"]["rs_2"]["b"]
                    self.data['rs_2']['raw'] = round(rs_2, 2)
                    self.data['rs_2']['scaled'] = round(a2 * rs_2 + b2, 2)
                    self.data['rs_2']['warning'] = 0
                    self.lcd_objs['rs_2'].set_text(f'{self.data["rs_2"]["scaled"]}')
                    if self.config["sensors"]['rs_2']["high_th"] and self.data['rs_2']["scaled"] > float(self.config["sensors"]['rs_2']["high_th"]):
                        self.add_sim800_job('send_alarm_sms', 'rs_2', True)
                        self.data['rs_2']['warning'] = 2
                        
                    if self.config["sensors"]['rs_2']["low_th"] and self.data['rs_2']["scaled"] < float(self.config["sensors"]['rs_2']["low_th"]):
                        self.add_sim800_job('send_alarm_sms', 'rs_2', False)
                        self.data['rs_2']['warning'] = 3
                else:
                    self.data['rs_2']['raw'] = None
                    self.data['rs_2']['scaled'] = None
                    self.data['rs_2']['warning'] = 1
                    self.lcd_objs['rs_2'].set_text('NC')
        except Exception as e:
            print_colored(f"Exception from rs485 handle:", Cyan)
            print_exception(e)
        finally:
            self.uart_lock = False
            self.last_rs485_update = time()
            self.rs485_update_running = False
            self.lcd_objs['rs_th'].set_style_text_color(lv_white, 0)
            if thread_lock.locked():
                thread_lock.release()
    
    def init_sd(self):
        print_colored('Init sd card')
        while self.spi_lock:
            sleep_ms(100)
        self.spi_lock = True
        spi2.init(baudrate=1320000, phase=0)
        
        try:
            sd = sdcard.SDCard(spi2, sd_cs)
            os.mount(sd, '/sd')
            self.sd_available = True
            self.data['sd_warning'] = 0
        except:
            print_colored('no sdcard detected')
            self.sd_available = False
            self.spi_lock = False
            self.data['sd_warning'] = 1
            return
        
        ls = os.listdir('/')
        if 'sd' not in ls:
            print_colored('SD not mounted, trying to mount sd...')
            try:
                os.mount(sd, '/sd')
                print_colored('Done')
            except:
                print_colored('failed to mount sd')
                self.sd_available = False
                self.spi_lock = False
                self.data['sd_warning'] = 1
                return
        
        
        ls = os.listdir('/sd')
        
        if 'config.json' in ls:
            with open('/sd/config.json') as f:
                config = json.load(f)
            result, msg = check_config(config)
            if result:
                save_config(config)
                print_colored('loaded config from sd card')
                try:
                    os.remove('/sd/config.json')
                except Exception as e:
                    print_colored('failed to remove config file on sd')
                    print_exception(e)
            else:
                print_colored(f'config on sd is invalid: {msg}')
        elif 'device_config.json' not in ls:
            config = load_config()
            with open('/sd/device_config.json', 'w') as f:
                json.dump(config, f)
            print_colored('saved config on sd card')
        if 'data' not in ls:
            print_colored('Creating "data" directory...')
            try:
                os.mkdir('/sd/data')
                print_colored('Done')
            except:
                print_colored('failed')
                self.sd_available = False
                self.spi_lock = False
                self.data['sd_warning'] = 1
                return
        
        ls = os.listdir('/sd/data')
        if 'raw' not in ls:
            print_colored('Creating "raw" directory...')
            try:
                os.mkdir('/sd/data/raw')
                print_colored('Done')
            except:
                print_colored('failed')
                self.sd_available = False
                self.spi_lock = False
                self.data['sd_warning'] = 1
                return
        
        if 'scaled' not in ls:
            print_colored('Creating "scaled" directory...')
            try:
                os.mkdir('/sd/data/scaled')
                print_colored('Done')
            except:
                print_colored('failed')
                self.sd_available = False
                self.spi_lock = False
                self.data['sd_warning'] = 1
                return
        self.spi_lock = False

    def log_data(self):
        print_colored('logging data to sd', Cyan)
        if not self.time_set:
            print_colored('Time not set', Cyan)
            self.last_sd_log = time()
            return
        self.lcd_objs['sd_th'].set_style_text_color(lv_green, 0)
        self.sd_log_running = True
        try:
            while self.spi_lock:
                sleep_ms(100)
            self.spi_lock = True
            spi2.init(baudrate=1320000, phase=0)
            
            tm = rtc.datetime()
            raw_filename = f'/sd/data/raw/{tm[0]}-{tm[1]:02d}-{tm[2]:02d}.csv'
            scaled_filename = f'/sd/data/scaled/{tm[0]}-{tm[1]:02d}-{tm[2]:02d}.csv'
            try:
                f = open(raw_filename)
                f.close()
            except:
                with open(raw_filename, 'w') as f:
                    f.write("timestamp")
                    for sensor in self.config['sensor_list']:
                        f.write(f',{sensor}')
                    f.write('\r\n')
            
            try:
                f = open(scaled_filename)
                f.close()
            except:
                with open(scaled_filename, 'w') as f:
                    f.write("timestamp")
                    for sensor in self.config['sensor_list']:
                        f.write(f',{sensor}')
                    f.write('\r\n')
            
            raw_file = open(raw_filename, 'a')
            scaled_file = open(scaled_filename, 'a')
            
            raw_file.write(f'{tm[0]}-{tm[1]:02d}-{tm[2]:02d} {tm[4]:02d}:{tm[5]:02d}')
            scaled_file.write(f'{tm[0]}-{tm[1]:02d}-{tm[2]:02d} {tm[4]:02d}:{tm[5]:02d}')
            thread_lock.acquire()
            for sensor in self.config['sensor_list']:
                
                if sensor in self.data:
                    if self.data[sensor]["raw"] is not None:
                        raw_file.write(f',{self.data[sensor]["raw"]:.2f}')
                    else:
                        raw_file.write(',')
                        
                    if self.data[sensor]["scaled"] is not None:
                        scaled_file.write(f',{self.data[sensor]["scaled"]:.2f}')
                    else:
                        scaled_file.write(',')
                else:
                    raw_file.write(',')
                    scaled_file.write(',')
                    
            thread_lock.release()
            
            raw_file.write('\r\n')
            scaled_file.write('\r\n')
            raw_file.close()
            scaled_file.close()
            print_colored('done', Cyan)
        except Exception as e:
            print_colored(f"Exception from log_data:", Cyan)
            print_exception(e)
        finally:
            self.last_sd_log = time()
            self.sd_log_running = False
            self.spi_lock = False
            self.lcd_objs['sd_th'].set_style_text_color(lv_white, 0)
            if thread_lock.locked():
                thread_lock.release()
    
    def generate_data_sms(self):
        tm = rtc.datetime()
        data_sms = f'{self.config["device_id"]},{tm[0]},{tm[1]:02d},{tm[2]:02d},{tm[4]:02d}'
        idx = 1
        while True:
            for sensor in self.config['sensors']:
                if (self.config['sensors'][sensor]['en']
                and self.config['sensors'][sensor]['sms_fun']
                and self.config['sensors'][sensor]['sms_ord'] == idx):
                    break
            else:
                break
            if self.data[sensor]["scaled"] is not None:
                data_sms += f',{self.data[sensor]["scaled"]:.2f}'
            else:
                data_sms += ','
                
            if self.config['sensors'][sensor]['sms_raw']:
                if self.data[sensor]["raw"] is not None:
                    data_sms += f'({self.data[sensor]["raw"]:.2f})'
                else:
                    data_sms += '()'
            idx += 1
        if 'bat' in self.data:
            data_sms += f',{round(self.data["bat"], 2)}'
        return data_sms
    
    def switch_uart_to(self, dst):
        while uart.any():
            uart.read()
        if dst == 'sim800':
            print_colored('switch uart to sim800')
            if self.uart_tx == _sim800_tx and self.uart_rx == _sim800_rx:
                print_colored('already on sim800')
                return
            machine.Pin(self.uart_tx, machine.Pin.OUT, value=1)
            machine.Pin(self.uart_rx, machine.Pin.OUT, value=1)
            uart.init(115200, tx=_sim800_tx, rx=_sim800_rx, rxbuf=2048)
            self.uart_tx = _sim800_tx
            self.uart_rx = _sim800_rx
            print_colored(f'switched to sim800 {self.uart_tx} {self.uart_rx} 115200')
            while uart.any():
                uart.read()
        elif dst == 'rs485':
            print_colored('switch to rs485')
            if self.uart_tx == _rs485_tx and self.uart_rx == _rs485_rx:
                print_colored('already on rs485')
                return
            machine.Pin(self.uart_tx, machine.Pin.OUT, value=1)
            machine.Pin(self.uart_rx, machine.Pin.OUT, value=1)
            uart.init(_rs485_baud, tx=_rs485_tx, rx=_rs485_rx)
            self.uart_tx = _rs485_tx
            self.uart_rx = _rs485_rx
            print_colored(f'switched to rs485 {self.uart_tx} {self.uart_rx} {_rs485_baud}')
            
    def init_modem(self):
        try:
            print_colored('check modem', Yellow)
            
            modem.check_reg()
            print_colored('modem is initialized', Yellow)
            return True
        except:
            try:
                print_colored('initializing modem', Yellow)
                
                modem.initialize()
                return True
            except:
                print_colored('failed to initialize modem', Yellow)
                return False
    
    def reset_timestamps(self):
        self.last_prcip_update = time()
        self.last_sdi12_update = time()
        self.last_pt100_update = time()
        self.last_rs485_update = time()
        self.last_update_check = time()
        self.last_ais_update   = time()
        self.last_sms_check    = time()
        self.last_data_post    = time()
        self.last_get_time     = time()
        self.last_data_sms     = time()
        self.last_sd_log       = float('-inf')
        self.last_loc_request  = time()
    
    def get_time(self, *args):
        print_colored('getting time', Yellow)
        if not self.config['gprs']['server']:
            self.last_get_time = time()
            print_colored('no server set', Yellow)
            return
        for _ in range(3):
            try:
                
                modem.connect(self.config['gprs']['apn'])
                
                result = modem.http_request(f'{self.config["gprs"]["server"]}/ahv_rtu/settings2.php?co={self.config["device_id"]}')
                
                modem.disconnect()
                
                if result.status_code == 200:
                    tm = list(map(int, result.content.split(',')))
                    rtc.datetime(tm)
                    self.create_old_percip_record()
                    self.time_set = True
                    print_colored(f'done {tm}', Yellow)
                    self.last_get_time = time()
                    self.reset_timestamps()
                    break
            except Exception as e:
                print_colored("Exception from get_time:", Yellow)
                print_exception(e)
                
    def get_location(self, *args):
        print_colored('getting location', Yellow)
        if not self.config['gprs']['server']:
            print_colored('no server set', Yellow)
            return
        eng_data = modem.get_eng_data()
#         eng_data = {'mccii': '432', 'mnc': '35', 'cellid': '5268', 'lac': '7747'}
        for _ in range(3):
            try:
                modem.connect(self.config['gprs']['apn'])
                result = modem.http_request(f'{self.config["gprs"]["server"]}/ahv_rtu/gps3.php', mode='POST', data=json.dumps(eng_data))
                modem.disconnect()
                if result.status_code == 200:
                    self.data['location'] = json.loads(result.content)
                    print_colored(f'done {self.data["location"]}', Yellow)
                    if self.data['location']['lat'] is not None:
                        self.lcd_objs['lat'].set_text(f'{self.data["location"]["lat"]}')
                        self.lcd_objs['lon'].set_text(f'{self.data["location"]["lon"]}')
                        self.lcd_objs['rad'].set_text(f'{self.data["location"]["radius"]}')
                    break
            except Exception as e:
                print_colored("Exception from get_location:", Yellow)
                print_exception(e)
    
    def check_for_sms(self, *args):
        print_colored('checking sms command...', Yellow)
                
        for i in range(1, 16):
            
            try:
                number, msg = modem.read_sms(i)
            except:
                self.last_sms_check = time()
                return
            
            modem.delete_sms(i)
            
            if '#stat' in msg:
                print_colored('stat sms received', Yellow)
                sms_data = self.generate_data_sms()
                modem.send_sms(number, sms_data)
                
            elif '#gp' in msg:
                print_colored('post sms received', Yellow)
                self.add_sim800_job('post_data', ())
            
            elif '#qu' in msg:
                print_colored('csq sms received', Yellow)
                csq = modem.get_signal_strength()
                modem.send_sms(number, f'{csq}')
                
            elif '#reset' in msg:
                print_colored('reset sms received', Yellow)
                sleep(1)
                machine.reset()
                
            elif '#update' in msg:
                print_colored('update sms received', Yellow)
                self.add_sim800_job('check_update', ())
            
            elif '#zero' in msg:
                print_colored('zero percip sms received', Yellow)
                self.zero_db()
            
            elif '#balance' in msg:
                print_colored('check balance sms received', Yellow)
                modem.ussd_code("*555*4*3*2#")
                result = modem.ussd_code("*555*1*2#")
                modem.send_sms(number, result)

            elif '007B0022006C006100740022' in msg:
                print_colored('location sms received', Yellow)
                try:
                    while '00' in msg:
                        msg = msg.replace('00', '')
                    loc = ''
                    for i in range(len(msg) / 2):
                        loc += chr(int(msg[2*i:2*i+2], 16))
                        
                    loc = json.loads(loc)
                    print_colored(loc, Yellow)
                    if 'ts' in loc:
                        rtc.datetime(list(map(int, loc['ts'].split(','))))
                        self.create_old_percip_record()
                        self.time_set = True
                        self.sms_time_set = True
                        self.reset_timestamps()
                    thread_lock.acquire()
                    self.data['location'] = {'lat':loc['lat'], 'lon':loc['lon']}
                    thread_lock.release()
                    print_colored(self.data['location'], Yellow)
                    self.add_sim800_job('send_gps_sms', ())
                    self.lcd_objs['lat'].set_text(f'{self.data["location"]["lat"]}')
                    self.lcd_objs['lon'].set_text(f'{self.data["location"]["lon"]}')
                except Exception as e:
                    print_colored('Failed parsing gps sms', Yellow)
                    print_exception(e)
                    self.last_sms_check = time()
                    if thread_lock.locked():
                        thread_lock.release()
          
    def post_data(self, *args):
        print_colored('Posting data', Yellow)
        if not self.config['gprs']['server']:
            self.last_data_post = time()
            print_colored('no server set', Yellow)
            return
        thread_lock.acquire()
        self.data['timestamp'] = time() + 946672200
        unenc_data = bytearray(json.dumps(self.data))
        thread_lock.release()
#         print(f'not encrypted data: {unenc_data}')
        
        key = ubinascii.unhexlify(self.config['enc']['key'])
        iv = mpyaes.generate_IV(16)
        aes = mpyaes.new(key, mpyaes.MODE_CBC, iv)
        aes.encrypt(unenc_data)
#         print(f'encrypted data: {unenc_data}')
        
        unenc_data = ubinascii.hexlify(iv + unenc_data)
        
        modem.connect(self.config['gprs']['apn'])
        
        result = modem.http_request(f"{self.config['gprs']['server']}/ahv_rtu/getdata_p2.php", mode='POST', data=f'data={unenc_data.decode()}', content_type='application/x-www-form-urlencoded')
        
        modem.disconnect()
        if result.status_code == 200:
            print_colored('done', Yellow)
            self.last_data_post = time()
        else:
            raise Exception("http request unsuccessful")

        
    def send_data_sms(self, *args):
        print_colored('sending data sms', Yellow)
        data = self.generate_data_sms()
        
        if self.config['sms']['phone_1']:
            print_colored('to phone #1', Yellow)
            modem.send_sms(self.config['sms']['phone_1'], data)
            print_colored('done', Yellow)
        sleep(1)
        if self.config['sms']['phone_2']:
            print_colored('to phone #2', Yellow)
            modem.send_sms(self.config['sms']['phone_2'], data)
            print_colored('done', Yellow)

        self.last_data_sms = time()
    
    def check_update(self, *args):
        print_colored('checking for update', Yellow)
        if not self.config['gprs']['server']:
            self.last_update_check = time()
            print_colored('no server set', Yellow)
            self.lcd_objs['status'].set_text("Server not set")
            return
        for _ in range(3):
            modem.connect(self.config['gprs']['apn'])
            result = modem.http_request(f'{self.config["gprs"]["server"]}/ahv_rtu2/version.php')
            try:
                new_version = float(result.content)
            except:
                new_version = 0
            if new_version <= _firmware_version:
                self.lcd_objs['status'].set_text("No updates found")
                print_colored('no update found', Yellow)
                modem.disconnect()
                break
            print_colored(f'new version found: {new_version}', Yellow)
            self.lcd_objs['status'].set_text(f"update found {new_version}")
            result = modem.download(f'{self.config["gprs"]["server"]}/ahv_rtu2/main_{new_version}.bin', f'main_{new_version}.py', lcd_obj=self.lcd_objs['status'])
            
            modem.disconnect()
            if result.status_code == 200:
                update_info = {'old_version':_firmware_version,
                               'del_old_file': False,
                               'new_version':new_version}
                with open('/update.json', 'w') as f:
                    json.dump(update_info, f)
                print_colored('restarting to apply update', Yellow)
                self.lcd_objs['status'].set_text("restarting to apply update")
                sleep(1)
                machine.reset()
        self.last_update_check = time()

        
    def send_loc_request_sms(self, *args):
        print_colored('sending location request sms', Yellow)

        cell_info = modem.get_eng_data()
        
        modem.send_sms("30004505003188", json.dumps(cell_info))
        print_colored('done', Yellow)
        self.loc_request_sms_sent = True
    
    def send_alarm_sms(self, sensor, is_high):
        print_colored(f'sending alarm sms for {sensor}', Yellow)
        tm = rtc.datetime()
        text = f"{tm[4]:02d}:{tm[5]:02d}:{tm[6]:02d}: {self.config['device_id']} -> Alarm! {self.config['sensors'][sensor]['disp_name']}'s " + \
               f"value is {self.data[sensor]['scaled']} and is {'higher' if is_high else 'lower'} than it's {'high' if is_high else 'low'} " + \
               f"threshold: {self.config['sensors'][sensor]['high_th'] if is_high else self.config['sensors'][sensor]['low_th']}"
        txt = self.lcd_objs['status'].get_text()
        if self.config['sms']['phone_1']:
            for _ in range(3):
                try:
                    self.lcd_objs['status'].set_text(f"{txt}phone_1")
                    print_colored('to phone_1', Yellow)
                    modem.send_sms(self.config['sms']['phone_1'], text)
                    print_colored('done', Yellow)
                    self.lcd_objs['status'].set_text(f"{txt}phone_1 done")
                    break
                except Exception as e:
                    self.lcd_objs['status'].set_text(f"{txt}phone_1 fail")
                    print_colored('failed', Yellow)
                    print_exception(e)
        
        sleep(1)
        if self.config['sms']['phone_2']:
            for _ in range(3):
                try:
                    self.lcd_objs['status'].set_text(f"{txt}phone_2")
                    print_colored('to phone_2', Yellow)
                    modem.send_sms(self.config['sms']['phone_2'], text)
                    print_colored('done', Yellow)
                    self.lcd_objs['status'].set_text(f"{txt}phone_2 done")
                    break
                except Exception as e:
                    self.lcd_objs['status'].set_text(f"{txt}phone_2 fail")
                    print_colored('failed', Yellow)
                    print_exception(e)
        self.lcd_objs['status'].set_text(txt)
    def send_gps_sms(self, *args):
        print_colored('sending gps sms', Yellow)
        data = self.generate_data_sms()
        data += f',{self.data["location"]["lat"]},{self.data["location"]["lon"]}'
        
        txt = self.lcd_objs['status'].get_text()
        if self.config['sms']['phone_1']:
            for _ in range(3):
                try:
                    self.lcd_objs['status'].set_text(f"{txt}phone_1")
                    print_colored('to phone_1', Yellow)
                    modem.send_sms(self.config['sms']['phone_1'], data)
                    print_colored('done', Yellow)
                    self.lcd_objs['status'].set_text(f"{txt}phone_1 done")
                    break
                except Exception as e:
                    self.lcd_objs['status'].set_text(f"{txt}phone_1 fail")
                    print_colored('failed', Yellow)
                    print_exception(e)
        sleep(1)
        if self.config['sms']['phone_2']:
            for _ in range(3):
                try:
                    self.lcd_objs['status'].set_text(f"{txt}phone_2")
                    print_colored('to phone_2', Yellow)
                    modem.send_sms(self.config['sms']['phone_2'], data)
                    print_colored('done', Yellow)
                    self.lcd_objs['status'].set_text(f"{txt}phone_2 done")
                    break
                except Exception as e:
                    self.lcd_objs['status'].set_text(f"{txt}phone_2 fail")
                    print_colored('failed', Yellow)
                    print_exception(e)
        self.lcd_objs['status'].set_text(txt)
    def add_sim800_job(self, name, *args):
        job = Job(name, getattr(self, name), args)
        if job not in self.sim800_jobs:
            self.sim800_jobs.append(job)
    
    def update_bat(self):
        tmp = 0
        for _ in range(10):
            tmp += bat.read_uv()
            sleep_ms(10)
        tmp /= 10
        tmp *= 0.00000475
        
        self.bat_label.set_text(f"{tmp:.2f} v")
        print_colored(f'battery voltage : {tmp}', Cyan)
        thread_lock.acquire()
        self.data['bat'] = tmp
        thread_lock.release()
    
    def loop(self):
        while True:
            feed_wdt()
            try:
                self.update_bat()
                if self.config['sensors']['ra']['en']:
                    if not self.prcip_update_running and time() - self.last_prcip_update > _prcip_update_interval:
                        _thread.start_new_thread(self.update_percip, ())
                
                if self.config['sensors']['pt']['en']:
                    if not self.pt100_update_running and time() - self.last_pt100_update > _pt100_update_interval:
                        self.update_pt100()
                
                if self.config['sdi12']['en']:
                    if not self.sdi12_update_running and time() - self.last_sdi12_update > _sdi12_update_interval:
                        self.update_sdi()
                
                if not self.ais_update_running and time() - self.last_ais_update > _ais_update_interval:
                    _thread.start_new_thread(self.update_ais, ())
                
                if self.config['rs485']['en']:
                    if not self.rs485_update_running and time() - self.last_rs485_update > _rs485_update_interval:
                        self.update_rs485()
                
                if self.sd_available:
                    if not self.sd_log_running and time() - self.last_sd_log > int(self.config['log']['interval']):
                        _thread.start_new_thread(self.log_data, ())
                
                if not self.sms_time_set:
                    if time() - self.last_get_time > _get_time_interval:
                        self.add_sim800_job('get_time', ())
                
                if 'location' not in self.data:
                    self.add_sim800_job('get_location', ())
                
                if time() - self.last_sms_check > _sms_check_interval:
                    self.add_sim800_job('check_for_sms', ())
                
                if self.config['gprs']['server']:
                    if time() - self.last_data_post > int(self.config['gprs']['interval']):
                        self.add_sim800_job('post_data', ())
                        
                if self.config['sms']['phone_1'] or self.config['sms']['phone_2']:
                    if time() - self.last_data_sms > int(self.config['sms']['interval']):
                        self.add_sim800_job('send_data_sms', ())
                
                if ('location' not in self.data or ('location' in self.data and self.data['location']['lat'] is None)):
                    if Job('get_location', None, ()) not in self.sim800_jobs and not self.loc_request_sms_sent:
                        self.add_sim800_job('send_loc_request_sms', ())
                        
                self.sim800_handler()
            except KeyboardInterrupt:
                break
            except Exception as e:
                print_colored("Exception from main loop:")
                print_exception(e)
            finally:
                sleep(3)

feed_wdt()
print_reset_cause()
print_colored(f'firmware version: {_firmware_version}')

main_app = App()
main_app.init_sd()
main_app.config = load_config()
result, msg = check_config(main_app.config)
if not result:
    print_colored(f'config invalid: {msg}')
    while True:
        feed_wdt()
        sleep(1)
main_app.data['device_id'] = main_app.config['device_id']
print_colored('config loaded successfully')
ap.config(essid=main_app.config['device_id'])
main_app.init_display()
main_app.init_sensors()
main_app.init_percip_db()

main_app.loop()