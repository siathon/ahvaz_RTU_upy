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
from ili9XXX import ili9341
import espidf as esp
from umodbus.modbus import ModbusRTU


Red='\033[0;31m'
Green='\033[0;32m'
Yellow='\033[0;33m'
Magenta='\033[0;35m'
Cyan='\033[0;36m'
White='\033[0;37m'

lv.log_register_print_cb(print)
disp = ili9341(mosi=11, miso=13, clk=12, cs=1, dc=9, rst=34, backlight=10, backlight_on=1, mhz=40, factor=32, hybrid=True, spihost=esp.VSPI_HOST, double_buffer=False)

_device_id = const(10132)
_firmware_version = 0.1
_write_percip_interval = const(300)  # s
_prcip_update_interval = const(30)   # s
_sdi12_update_interval = const(30)   # s
_pt100_update_interval = const(30)   # s
_rs485_update_interval = const(30)   # s
_check_update_interval = const(3600) # s
_loc_request_interval  = const(43200)# s
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
ap.config(essid=f'AHV{_device_id}')
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

spi2 = machine.SPI(2, 5000000, sck=machine.Pin(41), mosi=machine.Pin(40), miso=machine.Pin(42), phase=0)
sd_cs = machine.Pin(39, machine.Pin.OUT)
pt_cs = machine.Signal(sd_cs, invert=True)
pt = MAX31865(spi2, pt_cs, ref_resistor=470.0, wires=4)

AIs = {'a1': machine.ADC(machine.Pin(8),  atten=machine.ADC.ATTN_11DB),
       'a2': machine.ADC(machine.Pin(7),  atten=machine.ADC.ATTN_11DB),
       'a3': machine.ADC(machine.Pin(6),  atten=machine.ADC.ATTN_11DB),
       'c1': machine.ADC(machine.Pin(14), atten=machine.ADC.ATTN_11DB),
       'c2': machine.ADC(machine.Pin(3),  atten=machine.ADC.ATTN_11DB)}

uart = machine.UART(1, tx=_sim800_tx, rx=_sim800_rx, baudrate=115200)
modem = SIM800L.Modem(uart, _sim800_pw, MODEM_POWER_ON_PIN=_sim800_en)

modbus = ModbusRTU(0, uart, ctrl_pin=45)

# wdt = machine.# wdt(timeout=30000)
# wdt.feed()

thread_lock = _thread.allocate_lock()

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
    thread_lock.release()
    
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
        config['gprs']['url'] = data['gprs_url']
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
        config['rs485']['addr'] = int(data['rs485_addr']) if 'rs485_addr' in data else 0
        config['rs485']['baud'] = int(data['rs485_baud'])
        
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
                    config['sensors'][sensor]['high_th'] = round(float(data[f'{sensor}_high_th']), 2)
                else:
                    config['sensors'][sensor]['high_th'] = None
                    
                if f'{sensor}_low_th' in data:
                    config['sensors'][sensor]['low_th'] = round(float(data[f'{sensor}_low_th']), 2)
                else:
                    config['sensors'][sensor]['low_th'] = None
            else:
                config['sensors'][sensor]['en'] = 0
        save_config(config)
        msg = 'config saved successfully.'
        result = True

    except Exception as e:
        print_colored("Exception from handle form:", Magenta)
        print_exception(e)
        msg = 'Failed to save config'
        result = False

    request.Response.ReturnOkJSON({'msg': msg, 'result': result})
    

@WebRoute(GET, '/config.json')
def config(microWebSrv2, request):
    request.Response.ReturnFile('config.json')

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
        self.data['device_id'] = _device_id
        self.data['firmware_version'] = _firmware_version
        self.uart_tx = 18
        self.uart_rx = 17
        
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
        self.last_loc_request  = float('-inf')
        
        self.prcip_update_running = False
        self.sdi12_update_running = False
        self.pt100_update_running = False
        self.rs485_update_running = False
        self.ais_update_running   = False
        self.sd_log_running       = False
        self.server_running       = False
        
        self.next_ready = False
        self.prev_ready = False
        
        self.sms_time_set = False
        self.sim800_jobs = []
        
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
        self.tabview.set_size(240, 270)
        self.cur_tab = 0
        
        self.sdi_tab   = self.tabview.add_tab("sdi12")
        self.sdi_tab.set_style_pad_top(5, 0)
        self.ai_tab    = self.tabview.add_tab("AI")
        self.prcip_tab = self.tabview.add_tab("percip")
        self.rs_tab    = self.tabview.add_tab("rs485")
        self.info_tab  = self.tabview.add_tab("info")
        
        self.lcd_objs = {}
        
        self.time_timer = lv.timer_create(self.update_time, 1000, None)
        self.btn_timer = machine.Timer(2)
        self.btn_timer.init(mode=machine.Timer.PERIODIC, period=200, callback=self.scan_btns)
        
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

            if not self.init_modem():
                self.last_get_time = time()
                self.get_time_running = False
                self.uart_lock = False
                self.sim_handler_running = False
                return
            
            print_colored(f'running {self.sim800_jobs[0].name} job', Green)
            try:
                self.sim800_jobs[0].func(*self.sim800_jobs[0].args)
            except Exception as e:
                print_colored("Exception from sim800 handle:", Cyan)
                print_exception(e)
            finally:
                self.uart_lock = False
                thread_lock.acquire()
                self.sim800_jobs.pop(0)
                thread_lock.release()
        
    def init_sensors(self):
        if self.config['sensors']['ra']['en']:
            self.percip_cnt = 0
            self.data["ra"]    = {"raw": 0, "scaled": 0, "warning": 0}
            self.data["ra_1"]  = {"raw": 0, "scaled": 0, "warning": 0}
            self.data["ra_12"] = {"raw": 0, "scaled": 0, "warning": 0}
            self.percip_ready = False

        if self.config['sdi12']['en']:
            idx = 0
            for i in range(9):
                if self.config['sensors'][f's{i+1}']['en']:
                    label = lv.label(self.sdi_tab)
                    temp = f's{i+1}'
                    label.set_text(f"{self.config['sensors'][temp]['disp_name']}:")
                    label.set_pos(0, idx * 25)
                    label.set_size(100, 25)
                    label.set_style_text_font(lv.font_montserrat_18, 0)
                    self.data[f"s{i+1}"] = {"raw": None, "scaled": None, "warning": None}
                    label = lv.label(self.sdi_tab)
                    label.set_text('')
                    label.set_pos(105, idx * 25)
                    label.set_size(100, 25)
                    label.set_style_text_font(lv.font_montserrat_18, 0)
                    self.lcd_objs[f"s{i+1}"] = label
                    idx += 1
    
        if self.config['sensors']['pt']['en']:
            self.data["pt"] = {"raw": None, "scaled": None, "warning": None}
            
        if self.config['sensors']['a1']['en']:
            self.data["a1"] = {"raw": None, "scaled": None, "warning": None}
        
        if self.config['sensors']['a2']['en']:
            self.data["a2"] = {"raw": None, "scaled": None, "warning": None}
            
        if self.config['sensors']['a3']['en']:
            self.data["a3"] = {"raw": None, "scaled": None, "warning": None}
            
        if self.config['sensors']['c1']['en']:
            self.data["c1"] = {"raw": None, "scaled": None, "warning": None}
            
        if self.config['sensors']['c2']['en']:
            self.data["c2"] = {"raw": None, "scaled": None, "warning": None}
        
        if self.config['rs485']['en']:
            modbus._addr_list = [self.config['rs485']['addr']]
            _rs485_baud = self.config['rs485']['baud']
            
            if self.config['sensors']['rs_1']['en']:
                self.data["rs_1"] = {"raw": None, "scaled": None, "warning": None}
                
            if self.config['sensors']['rs_2']['en']:
                self.data["rs_2"] = {"raw": None, "scaled": None, "warning": None}

        self.pin_timer = machine.Timer(1)
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
                
    def go_to_next_tab(self):
        self.cur_tab += 1
        if self.cur_tab == 5:
            self.cur_tab = 0
        self.tabview.set_act(self.cur_tab, 0)
        if self.cur_tab == 4:
            ap.active(True)
            app.StartManaged()
        elif app.IsRunning:
            app.Stop()
            ap.active(False)
        
    def go_to_previous_tab(self):
        self.cur_tab -= 1
        if self.cur_tab == -1:
            self.cur_tab = 4
        self.tabview.set_act(self.cur_tab, 0)
        if self.cur_tab == 4:
            ap.active(True)
            app.StartManaged()
        elif app.IsRunning:
            app.Stop()
            ap.active(False)
            
    def init_percip_db(self):
        try:
            self.db_file = open("percip.db", "r+b")
        except OSError:
            self.db_file = open("percip.db", "w+b")
            
        self.db = btree.open(self.db_file)
        self.percip_cnt = 0
        keys = list(self.db)
        
        if keys:
            self.percip_cnt = int.from_bytes(self.db[keys[-1]], 'big')
        
    def update_percip(self):
        self.prcip_update_running = True

        try:
            a = self.config["sensors"]["ra"].get("a", 1.0)
            b = self.config["sensors"]["ra"].get("b", 0.0)

            tm = roundup(time())
            pr_to = self.percip_cnt
            thread_lock.acquire()
            keys = list(self.db)
            if len(keys) >= 300:
                print_colored(f"dblen: {len(keys)} remove 10 keys from db", Cyan)
                for i in range(10):
                    del self.db[keys[i]]
            self.db[tm.to_bytes(4, 'big')] = pr_to.to_bytes(4, 'big')
            self.db.flush()
            self.db_file.flush()
            tm_1h = tm - 3600
            
            # wdt.feed()
            
            while tm > tm_1h:
                b_tm = tm_1h.to_bytes(4, 'big')
                if b_tm in self.db:
                    pr_1h = pr_to - int.from_bytes(self.db[b_tm], 'big')
                    break
                tm_1h += _write_percip_interval
            else:
                pr_1h = 0
                
            # wdt.feed()
            
            tm_12 = tm - 43200
            while tm > tm_12:
                b_tm = tm_12.to_bytes(4, 'big')
                if b_tm in self.db:
                    pr_12 = pr_to - int.from_bytes(self.db[b_tm], 'big')
                    break
                tm_12 += _write_percip_interval
            else:
                pr_12 = 0
            
            self.data["ra"]["raw"] = round(pr_to, 2)
            self.data["ra"]["scaled"] = round(a * pr_to + b, 2)
            self.data["ra_1"]["raw"] = round(pr_1h, 2)
            self.data["ra_1"]["scaled"] = round(a * pr_1h + b, 2)
            self.data["ra_12"]["raw"] = round(pr_12, 2)
            self.data["ra_12"]["scaled"] = round(a * pr_12 + b, 2)
            
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
            thread_lock.release()
        except Exception as e:
            print_colored("Exception from percip handle:", Cyan)
            print_exception(e)
        finally:
            self.last_prcip_update = time()
            self.prcip_update_running = False
            if thread_lock.locked():
                thread_lock.release()
    
    def zero_db(self):
        thread_lock.acquire()
        keys = list(self.db)
        for key in keys:
            del self.db[key]
        self.percip_cnt = 0
        self.db.flush()
        self.db_file.flush()
        thread_lock.release()
    
    def update_sdi(self):
        self.sdi12_update_running = True

        addr = str(self.config['sdi12'].get('addr', '0'))
        while True:
            try:
                # wdt.feed()
                result = sdi12.measure_data(addr, request_crc = True)
                print_colored(f'sdi {addr} data: {result}', Cyan)
                if result[0] > 0:
                    cnt, data = result
                    break
                sleep(1)
            except:
                sleep(1)
                continue
        else:
            cnt = 0
            data = []
        
        if data:
            try:
                thread_lock.acquire()
                for i in range(9):
                    if self.config['sensors'][f's{i+1}']['en']:
                        temp = f's{i+1}'
                        a = float(self.config['sensors'][temp]['a'])
                        b = float(self.config['sensors'][temp]['b'])
                        self.data[temp]['raw'] = round(data[i], 2)
                        self.data[temp]['scaled'] = round(a * data[i] + b, 2)
                        
                        if self.config["sensors"][temp]["high_th"] and self.data[temp]["scaled"] > float(self.config["sensors"][temp]["high_th"]):
                            self.add_sim800_job('send_alarm_sms', temp, True)
                        
                        if self.config["sensors"][temp]["low_th"] and self.data[temp]["scaled"] < float(self.config["sensors"][temp]["low_th"]):
                            self.add_sim800_job('send_alarm_sms', temp, False)
#                         self.lcd_objs[temp].set_text(f"{self.data[temp]['scaled']}")
                
            except Exception as e:
                print_colored(f"Exception from sdi12 update:", Cyan)
                print_exception(e)
            finally:
                self.last_sdi12_update = time()
                self.sdi12_update_running = False
                if thread_lock.locked():
                    thread_lock.release()
                        
    def update_pt100(self):
        self.pt100_update_running = True

        try:
            a = self.config["sensors"]["pt"].get("a", 1.0)
            b = self.config["sensors"]["pt"].get("b", 0.0)

            while self.spi_lock:
                sleep_ms(100)
            self.spi_lock = True
            spi2.init(phase=1)
            tmp = pt.temperature
            print_colored(f'pt100: {tmp}', Cyan) 
            self.spi_lock = False
            thread_lock.acquire()
            self.data['pt']['raw'] = round(tmp, 2)
            self.data['pt']['scaled'] = round(a * tmp + b, 2)
            
            if self.config["sensors"]['pt']["high_th"] and self.data['pt']["scaled"] > float(self.config["sensors"]['pt']["high_th"]):
                self.add_sim800_job('send_alarm_sms', 'pt', True)
            
            if self.config["sensors"]['pt']["low_th"] and self.data['pt']["scaled"] < float(self.config["sensors"]['pt']["low_th"]):
                self.add_sim800_job('send_alarm_sms', 'pt', False)

        except Exception as e:
            print_colored(f"Exception from pt100 handle:", Cyan)
            print_exception(e)
        finally:
            self.last_pt100_update = time()
            self.pt100_update_running = False
            if thread_lock.locked():
                thread_lock.release()

    def update_ais(self):
        self.ais_update_running = True

        try:
            for sensor in ['a1', 'a2', 'a3']:
                # wdt.feed()
                if sensor in self.config['sensors'] and self.config['sensors'][sensor]['en']:
                    a = self.config["sensors"][sensor].get("a", 1.0)
                    b = self.config["sensors"][sensor].get("b", 0.0)
                    tmp = 0
                    for _ in range(10):
                        tmp += AIs[sensor].read_u16()
                        sleep_ms(10)
                    tmp /= 10
                    tmp = tmp * 0.000004636636 - (tmp * 0.000000022)
                    thread_lock.acquire()
                    self.data[sensor]['raw'] = round(tmp, 2)                
                    self.data[sensor]['scaled'] = round(a * tmp + b, 2)
                    print_colored(f'{sensor} : {self.data[sensor]}', Cyan)

                    if self.config["sensors"][sensor]["high_th"] and self.data[sensor]["scaled"] > float(self.config["sensors"][sensor]["high_th"]):
                        self.add_sim800_job('send_alarm_sms', sensor, True)
                    
                    if self.config["sensors"][sensor]["low_th"] and self.data[sensor]["scaled"] < float(self.config["sensors"][sensor]["low_th"]):
                        self.add_sim800_job('send_alarm_sms', sensor, False)
                    
                    thread_lock.release()
            for sensor in ['c1', 'c2']:
                # wdt.feed()
                if sensor in self.config['sensors'] and self.config['sensors'][sensor]['en']:
                    a = self.config["sensors"][sensor].get("a", 1.0)
                    b = self.config["sensors"][sensor].get("b", 0.0)
                    tmp = 0
                    for _ in range(10):
                        tmp += AIs[sensor].read_u16()
                        sleep_ms(10)
                    tmp /= 1200000
                    thread_lock.acquire()
                    self.data[sensor]['raw'] = round(tmp, 2)                
                    self.data[sensor]['scaled'] = round(a * tmp + b, 2)
                    print_colored(f'{sensor} : {self.data[sensor]}', Cyan)
                    
                    if self.config["sensors"][sensor]["high_th"] and self.data[sensor]["scaled"] > float(self.config["sensors"][sensor]["high_th"]):
                        self.add_sim800_job('send_alarm_sms', sensor, True)
                    
                    if self.config["sensors"][sensor]["low_th"] and self.data[sensor]["scaled"] < float(self.config["sensors"][sensor]["low_th"]):
                        self.add_sim800_job('send_alarm_sms', sensor, False)
                    thread_lock.release()
        except Exception as e:
            print_colored(f"Exception from AIs handle:", Cyan)
            print_exception(e)
        finally:
            self.last_ais_update = time()
            self.ais_update_running = False
            if thread_lock.locked():
                thread_lock.release()
    
    def update_rs485(self):
        self.rs485_update_running = True

        try:
            a1 = self.config["sensors"]["rs_1"]["a"]
            b1 = self.config["sensors"]["rs_1"]["b"]
            a2 = self.config["sensors"]["rs_2"]["a"]
            b2 = self.config["sensors"]["rs_2"]["b"]
            addr = self.config['rs485']['addr']
            while self.uart_lock:
                sleep_ms(100)
            self.uart_lock = True
            self.switch_uart_to('rs485')
            
            rs1, rs2 = modbus._itf.read_holding_registers(addr, 1, 2)
            print_colored(f'rs485: {rs1} {rs2}', Cyan) 
            self.uart_lock = False
            
            thread_lock.acquire()
            if self.config["sensors"]["rs_1"]['en']:
                self.data['rs_1']['raw'] = round(rs1, 2)
                self.data['rs_1']['scaled'] = round(a1 * rs1 + b1, 2)
                
            if self.config["sensors"]["rs_2"]['en']:
                self.data['rs_2']['raw'] = round(rs2, 2)
                self.data['rs_2']['scaled'] = round(a2 * rs2 + b2, 2)
            
            if self.config["sensors"]['rs_1']["high_th"] and self.data['rs_1']["scaled"] > float(self.config["sensors"]['rs_1']["high_th"]):
                self.add_sim800_job('send_alarm_sms', 'rs_1', True)
            
            if self.config["sensors"]['rs_1']["low_th"] and self.data['rs_1']["scaled"] < float(self.config["sensors"]['rs_1']["low_th"]):
                self.add_sim800_job('send_alarm_sms', 'rs_1', False)
                
            if self.config["sensors"]['rs_2']["high_th"] and self.data['rs_2']["scaled"] > float(self.config["sensors"]['rs_2']["high_th"]):
                self.add_sim800_job('send_alarm_sms', 'rs_2', True)
            
            if self.config["sensors"]['rs_2']["low_th"] and self.data['rs_2']["scaled"] < float(self.config["sensors"]['rs_2']["low_th"]):
                self.add_sim800_job('send_alarm_sms', 'rs_2', False)

        except Exception as e:
            print_colored(f"Exception from rs485 handle:", Cyan)
            print_exception(e)
        finally:
            self.uart_lock = False
            self.last_rs485_update = time()
            self.rs485_update_running = False
            if thread_lock.locked():
                thread_lock.release()
    
    def init_sd(self):
        while self.spi_lock:
            sleep_ms(100)
        self.spi_lock = True
        spi2.init(phase=0)
        # wdt.feed()
        try:
            sd = sdcard.SDCard(spi2, sd_cs)
            os.mount(sd, '/sd')
            self.sd_available = True
            self.data['sd_warning'] = 0
        except:
            self.sd_available = False
            self.spi_lock = False
            self.data['sd_warning'] = 1
            return
        # wdt.feed()
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
        # wdt.feed()
        ls = os.listdir('/sd')
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
        # wdt.feed()
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
        # wdt.feed()
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
        
        self.sd_log_running = True
        try:
            while self.spi_lock:
                sleep_ms(100)
            self.spi_lock = True
            spi2.init(phase=0)
            # wdt.feed()
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
            # wdt.feed()
            try:
                f = open(scaled_filename)
                f.close()
            except:
                with open(scaled_filename, 'w') as f:
                    f.write("timestamp")
                    for sensor in self.config['sensor_list']:
                        f.write(f',{sensor}')
                    f.write('\r\n')
            # wdt.feed()
            raw_file = open(raw_filename, 'a')
            scaled_file = open(scaled_filename, 'a')
            
            raw_file.write(f'{tm[0]}-{tm[1]:02d}-{tm[2]:02d} {tm[4]:02d}:{tm[5]:02d}')
            scaled_file.write(f'{tm[0]}-{tm[1]:02d}-{tm[2]:02d} {tm[4]:02d}:{tm[5]:02d}')
            thread_lock.acquire()
            for sensor in self.config['sensor_list']:
                # wdt.feed()
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
        except Exception as e:
            print_colored(f"Exception from log_data:", Cyan)
            print_exception(e)
        finally:
            self.last_sd_log = time()
            self.sd_log_running = False
            self.spi_lock = False
            if thread_lock.locked():
                thread_lock.release()
    
    def generate_data_sms(self):
        tm = rtc.datetime()
        data_sms = f'{_device_id},{tm[0]},{tm[1]:02d},{tm[2]:02d},{tm[4]:02d}'
        idx = 1
        while True:
            for sensor in self.config['sensors']:
                if (self.config['sensors'][sensor]['en']
                and self.config['sensors'][sensor]['sms_fun']
                and self.config['sensors'][sensor]['sms_ord'] == idx):
                    break
            else:
                break
            if self.data[sensor]["scaled"]:
                data_sms += f',{self.data[sensor]["scaled"]:.2f}'
            else:
                data_sms += ','
                
            if self.config['sensors'][sensor]['sms_raw']:
                if self.data[sensor]["raw"]:
                    data_sms += f'({self.data[sensor]["raw"]:.2f})'
                else:
                    data_sms += '()'
            idx += 1
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
            uart.init(115200, tx=_sim800_tx, rx=_sim800_rx)
            self.uart_tx = _sim800_tx
            self.uart_rx = _sim800_rx
            print_colored(f'switched to sim800 {self.uart_tx} {self.uart_rx} 115200')

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
            # wdt.feed()
            modem.check_reg()
            print_colored('modem is initialized', Yellow)
            return True
        except:
            try:
                print_colored('initializing modem', Yellow)
                # wdt.feed()
                modem.initialize()
                return True
            except:
                print_colored('failed to initialize modem', Yellow)
                return False
        
    def get_time(self, *args):
        print_colored('getting time', Yellow)
        # wdt.feed()
        for _ in range(3):
            try:
                # wdt.feed()
                modem.connect(self.config['gprs']['apn'])
                # wdt.feed()
                result = modem.http_request('http://gw.abfascada.ir/ahv_rtu/settings2.php')
                # wdt.feed()
                modem.disconnect()
                # wdt.feed()
                if result.status_code == 200:
                    tm = list(map(int, result.content.split(',')))
                    rtc.datetime(tm)
                    print_colored(f'done {tm}', Yellow)
                    self.last_get_time = time()
                    break
            except Exception as e:
                print_colored("Exception from get_time:", Yellow)
                print_exception(e)
            
        
    def check_for_sms(self, *args):
        print_colored('checking sms command...', Yellow)
                
        for i in range(1, 16):
            # wdt.feed()
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
                self.post_data()
            
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
                self.update()
            
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
                    if 'ts' in loc:
                        rtc.datetime(list(map(int, loc['ts'].split(','))))
                        self.sms_time_set = True
                    thread_lock.acquire()
                    self.data['location'] = {'lat':loc['lat'], 'lon':loc['lon']}
                    thread_lock.release()
                    print_colored(self.data['location'], Yellow)
                    self.add_sim800_job('send_gps_sms', ())
                except Exception as e:
                    print_colored('Failed parsing gps sms', Yellow)
                    print_exception(e)
                    self.last_sms_check = time()
                    if thread_lock.locked():
                        thread_lock.release()
          
    def post_data(self, *args):
        print_colored('Posting data', Yellow)
        if not self.config['gprs']['url']:
            self.last_data_post = time()
            print_colored('no url set', Yellow)
            return
        thread_lock.acquire()
        self.data['timestamp'] = time() + 946672200
        unenc_data = bytearray(json.dumps(self.data))
        thread_lock.release()
#         print(f'not encrypted data: {unenc_data}')
        # wdt.feed()
        key = ubinascii.unhexlify(self.config['enc']['key'])
        iv = mpyaes.generate_IV(16)
        aes = mpyaes.new(key, mpyaes.MODE_CBC, iv)
        aes.encrypt(unenc_data)
#         print(f'encrypted data: {unenc_data}')
        
        unenc_data = ubinascii.hexlify(iv + unenc_data)
        
        try:
            for _ in range(3):
                # wdt.feed()
                modem.connect(self.config['gprs']['apn'])
                # wdt.feed()
                result = modem.http_request(self.config['gprs']['url'], mode='POST', data=f'data={unenc_data.decode()}', content_type='application/x-www-form-urlencoded')
                # wdt.feed()
                modem.disconnect()
                if result.status_code == 200:
                    print_colored('done', Yellow)
                    break
        except Exception as e:
            print_colored("Exception from post_data:", Yellow)
            print_exception(e)
        finally:
            self.last_data_post = time()
        
    def send_data_sms(self, *args):
        print_colored('sending data sms', Yellow)
        data = self.generate_data_sms()
        # wdt.feed()
        try:
            if self.config['sms']['phone_1']:
                print_colored('to phone #1', Yellow)
                modem.send_sms(self.config['sms']['phone_1'], data)
            # wdt.feed()
            sleep(1)
            if self.config['sms']['phone_2']:
                print_colored('to phone #2', Yellow)
                modem.send_sms(self.config['sms']['phone_2'], data)
        except Exception as e:
            print_colored("Exception from send_data_sms:", Yellow)
            print_exception(e)
        finally:
            self.last_data_sms = time()
    
    def check_update(self, *args):
        print_colored('checking for update', Yellow)
        
        try:
            for _ in range(3):
                # wdt.feed()
                modem.connect(self.config['gprs']['apn'])
                # wdt.feed()
                result = modem.http_request('http://fw.abfascada.ir/ahv_rtu2/version.php')
                try:
                    new_version = float(result.content)
                except:
                    new_version = 0
                if new_version <= _firmware_version:
                    print_colored('no update found', Yellow)
                    modem.disconnect()
                    break
                print_colored(f'new version found: {new_version}', Yellow)
                # wdt.feed()
                result = modem.download(f'http://fw.abfascada.ir/ahv_rtu2/main_{new_version}.bin', f'main_{new_version}.py')
                # wdt.feed()
                modem.disconnect()
                if result.status_code == 200:
                    update_info = {'old_version':_firmware_version,
                                   'new_version':new_version}
                    with open('/update.json', 'w') as f:
                        json.dump(update_info, f)
                    print_colored('restarting to apply update', Yellow)
                    sleep(1)
                    machine.reset()
                    break
        except Exception as e:
            print_colored("Exception from check_update:", Yellow)
            print_exception(e)
        finally:
            self.last_update_check = time()

        
    def send_loc_request_sms(self, *args):
        print_colored('sending location request sms', Yellow)
        
        try:
            # wdt.feed()
            cell_info = modem.get_eng_data()
            # wdt.feed()
            modem.send_sms("30004505003188", json.dumps(cell_info))
            print_colored('done', Yellow)
        except Exception as e:
            print_colored('Exception from send loc request sms:', Yellow)
            print_exception(e)
        finally:
            self.last_loc_request = time()
    
    def send_alarm_sms(self, sensor, is_high):
        print_colored(f'sending alarm sms for {sensor}', Yellow)
        try:
            tm = rtc.datetime()
            text = f"{tm[4]:02d}:{tm[5]:02d}:{tm[6]:02d}: {_device_id} -> Alarm! {self.config['sensors'][sensor]['disp_name']}'s " + \
                   f"value is {self.data[sensor]['scaled']} and is {'higher' if is_high else 'lower'} than it's {'high' if is_high else 'low'} " + \
                   f"threshold: {self.config['sensors'][sensor]['high_th'] if is_high else self.config['sensors'][sensor]['low_th']}"
            if self.config['sms']['phone_1']:
                print_colored('to phone #1', Yellow)
                modem.send_sms(self.config['sms']['phone_1'], text)
                print_colored('done', Yellow)
            # wdt.feed()
            sleep(1)
            if self.config['sms']['phone_2']:
                print_colored('to phone #2', Yellow)
                modem.send_sms(self.config['sms']['phone_2'], text)
                print_colored('done', Yellow)
        except Exception as e:
            print_colored('Exception from send loc request sms:', Yellow)
            print_exception(e)
            
    def send_gps_sms(self, *args):
        print_colored('sending data sms', Yellow)
        data = self.generate_data_sms()
        data += f',{self.data["location"]["lat"]},{self.data["location"]["lon"]}'
        # wdt.feed()
        try:
            if self.config['sms']['phone_1']:
                print_colored('to phone #1', Yellow)
                modem.send_sms(self.config['sms']['phone_1'], data)
            # wdt.feed()
            sleep(1)
            if self.config['sms']['phone_2']:
                print_colored('to phone #2', Yellow)
                modem.send_sms(self.config['sms']['phone_2'], data)
        except Exception as e:
            print_colored("Exception from send_data_sms:", Yellow)
            print_exception(e)
            
    def add_sim800_job(self, name, *args):
        job = Job(name, getattr(self, name), args)
        if job not in self.sim800_jobs:
            thread_lock.acquire()
            self.sim800_jobs.append(job)
            thread_lock.release()
    
    def loop(self):
        while True:
            try:
                if self.config['sensors']['ra']['en']:
                    if not self.prcip_update_running and time() - self.last_prcip_update > _prcip_update_interval:
                        _thread.start_new_thread(self.update_percip, ())
                
                if self.config['sensors']['pt']['en']:
                    if not self.pt100_update_running and time() - self.last_pt100_update > _pt100_update_interval:
                        _thread.start_new_thread(self.update_pt100, ())
                
                if self.config['sdi12']['en']:
                    if not self.sdi12_update_running and time() - self.last_sdi12_update > _sdi12_update_interval:
                        _thread.start_new_thread(self.update_sdi, ())
                
                if not self.ais_update_running and time() - self.last_ais_update > _ais_update_interval:
                    _thread.start_new_thread(self.update_ais, ())
                
                if self.config['rs485']['en']:
                    if not self.rs485_update_running and time() - self.last_rs485_update > _rs485_update_interval:
                        _thread.start_new_thread(self.update_rs485, ())
                
                if self.sd_available:
                    if not self.sd_log_running and time() - self.last_sd_log > int(self.config['log']['interval']):
                        _thread.start_new_thread(self.log_data, ())
                    
                if time() - self.last_sms_check > _sms_check_interval:
                    self.add_sim800_job('check_for_sms', ())
                
                if self.config['gprs']['url']:
                    if time() - self.last_data_post > int(self.config['gprs']['interval']):
                        self.add_sim800_job('post_data', ())
                        
                if self.config['sms']['phone_1'] or self.config['sms']['phone_2']:
                    if time() - self.last_data_sms > int(self.config['sms']['interval']):
                        self.add_sim800_job('send_data_sms', ())
                        
                if time() - self.last_loc_request > _loc_request_interval:
                    self.add_sim800_job('send_loc_request_sms', ())
                    
                if not self.sms_time_set:
                    if time() - self.last_get_time > _get_time_interval:
                        self.add_sim800_job('get_time', ())
                        
                self.sim800_handler()
            except Exception as e:
                print_colored("Exception from main loop:")
                print_exception(e)
            finally:
                sleep(5)

# wdt.feed()
print_reset_cause()
print_colored(f'firmware version: {_firmware_version}')
# wdt.feed()
main_app = App()
main_app.init_sd()
main_app.config = load_config()
main_app.init_display()
main_app.init_sensors()
main_app.init_percip_db()
# wdt.feed()
main_app.add_sim800_job('get_time', ())
sleep_ms(100)
main_app.loop()