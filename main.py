import micropython
import json
import _thread
from time import sleep, sleep_ms, time
from max31865 import MAX31865
import machine
import SIM800L
from sys import print_exception
import btree
from micropython import const
from SDI12 import SDI12
import sdcard
import ubinascii
import mpyaes
from microdot_asyncio import Microdot, redirect, send_file, Response
from network import WLAN, AP_IF
import gc
# import lvgl as lv
# from ili9XXX import ili9341
# import espidf as esp
# import uasyncio

# disp = ili9341(mosi=11, miso=13, clk=12, cs=1, dc=9, rst=34, mhz=40, factor=64, hybrid=False, spihost=esp.VSPI_HOST)

_device_id = const(10001)
_firmware_version = 0.1
_write_percip_interval = const(300)  # s
_prcip_update_interval = const(30)   # s
_sdi12_update_interval = const(30)   # s
_pt100_update_interval = const(30)   # s
_check_update_interval = const(3600) # s
_loc_request_interval  = const(43200)# s
_ais_update_interval   = const(30)   # s
_sms_check_interval    = const(300)  # s
_get_time_interval     = const(3600) # s

_sim800_tx = const(18)
_sim800_rx = const(17)
_sim800_en = const(38)
_sim800_pw = const(2)
_rs485_tx  = const(36)
_rs485_rx  = const(35)

ap = WLAN(AP_IF)
ap.active(True)
ap.config(essid=f'AHV{_device_id}')
ap.config(max_clients=1)

rtc = machine.RTC()

# led = machine.PWM(machine.Pin(37))
# led.freq(1)
# led.duty(102)

led = machine.Pin(37, machine.Pin.OUT)

sdi12 = SDI12(0, 5)

percip = machine.Pin(4, machine.Pin.IN, machine.Pin.PULL_UP)

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
rs_ctl = machine.Pin(45, machine.Pin.OUT)
rs_ctl.value(1)

# wdt = machine.# wdt(timeout=30000)
# wdt.feed()
def print_reset_cause():
    code = machine.reset_cause()
    print('reset reason: ', end='')
    if code == machine.PWRON_RESET:
        print('Power ON')
    elif code == machine.HARD_RESET:
        print('Hard reset')
    elif code == machine.WDT_RESET:
        print('Watchdog rset')
    elif code == machine.DEEPSLEEP_RESET:
        print('Deep sleep reset')
    elif code == machine.SOFT_RESET:
        print('Soft reser')
    else:
        print('Unknown')

def save_config(config:dict) -> int:
#     if save_on_sd:
#         try:
#             with open('/sd/config.json', 'w') as f:
#                 json.dump(config, f)
#         except:
#             print("cannot save config on SD")

    try:
        with open('/config.json', 'w') as f:
            json.dump(config, f)
            return 0
    except:
        print("Failed to save config on flash")
        return -1

def load_config() -> dict:
#     try:
#         with open('/sd/config.json') as f:
#             config = json.load(f)
#         save_config(config, False)
#         return config
#     except:
#         print("No config on SD")

    try:
        with open('/config.json') as f:
            config = json.load(f)
#             if sd_available:
#                 save_config(config, sd_available)
            return config
    except:
        return {}

def roundup(num_to_round) -> int:
    rm = num_to_round % _write_percip_interval
    if rm == 0:
        return num_to_round
    return num_to_round + _write_percip_interval - rm

app = Microdot()

@app.route('/', methods=['GET', 'POST'])
def index(request):
    if request.method == 'POST':
        data = request.json
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
            print("Exception from handle form:")
            print_exception(e)
            msg = 'Failed to save config'
            result = False
        return Response(body={'msg': msg, 'result': result})
    return send_file('/index.html')

@app.route('/config.json')
def config(request):
    return send_file('config.json')

@app.errorhandler(404)
def not_found(request):
    return redirect('/')

class App:
    def __init__(self):
        self.thread_lock = _thread.allocate_lock()
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
        self.update_check_running = False
        self.ais_update_running   = False
        self.sms_check_running    = False
        self.data_post_running    = False
        self.get_time_running     = False
        self.data_sms_running     = False
        self.sd_log_running       = False
        
        self.sms_time_set = False
        
    def init_sensors(self):
        if self.config['sensors']['ra']['en']:
            self.percip_cnt = 0
            self.data["ra"]    = {"raw": 0, "scaled": 0, "warning": 0}
            self.data["ra_1"]  = {"raw": 0, "scaled": 0, "warning": 0}
            self.data["ra_12"] = {"raw": 0, "scaled": 0, "warning": 0}
            self.percip_ready = False

        if self.config['sdi12']['en']:
            for i in range(9):
                if self.config['sensors'][f's{i+1}']['en']:
                    self.data[f"s{i+1}"] = {"raw": None, "scaled": None, "warning": None}
    
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

        self.pin_timer = machine.Timer(0)
        self.pin_timer.init(mode=machine.Timer.PERIODIC, period=100, callback=self.scan_pins)
        print("started scan pins timer")
            
    def scan_pins(self, t):
        try:
            if percip.value():
                self.percip_ready = True
            
            if not percip.value() and self.percip_ready:
                self.percip_ready = False
                self.percip_cnt += 1
                
        except Exception as e:
            print(f"Exception from scan pins: {e}")
            
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
            gc.collect()
            tm = roundup(time())
            pr_to = self.percip_cnt
            keys = list(self.db)
            if len(keys) >= 300:
                print(f"dblen: {len(keys)} remove 10 keys from db")
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
            self.thread_lock.acquire()
            self.data["ra"]["raw"] = round(pr_to, 2)
            self.data["ra"]["scaled"] = round(a * pr_to + b, 2)
            self.data["ra_1"]["raw"] = round(pr_1h, 2)
            self.data["ra_1"]["scaled"] = round(a * pr_1h + b, 2)
            self.data["ra_12"]["raw"] = round(pr_12, 2)
            self.data["ra_12"]["scaled"] = round(a * pr_12 + b, 2)
            self.thread_lock.release()

        except Exception as e:
            print("Exception from percip handle:")
            print_exception(e)
        self.last_prcip_update = time()
        self.prcip_update_running = False
    
    def zero_db(self):
        keys = list(self.db)
        for key in keys:
            del self.db[key]
        self.percip_cnt = 0
            
        self.db.flush()
        self.db_file.flush()
    
    def update_sdi(self):
        self.sdi12_update_running = True
        addr = str(self.config['sdi12'].get('addr', '0'))
        while True:
            try:
                # wdt.feed()
                result = sdi12.measure_data(addr, request_crc = True)
                print(f'sdi {addr} data: {result}')
                if result[0] > 0:
                    cnt, data = result
                    break
                await uasyncio.sleep(1)
            except:
                await uasyncio.sleep(1)
                continue
        else:
            cnt = 0
            data = []
        
        if data:
            try:
                self.thread_lock.acquire()
                for i in range(9):
                    if self.config['sensors'][f's{i+1}']['en']:
                        a = float(self.config['sensors'][f's{i+1}']['a'])
                        b = float(self.config['sensors'][f's{i+1}']['b'])
                        self.data[f's{i+1}']['raw'] = round(data[i], 2)
                        self.data[f's{i+1}']['scaled'] = round(a * data[i] + b, 2)
                self.thread_lock.release()
                
            except Exception as e:
                self.thread_lock.release()
                print(f"Exception from sdi12 update:")
                print_exception(e)
        
        self.last_sdi12_update = time()
        self.sdi12_update_running = False
                        
    def update_pt100(self):
        self.pt100_update_running = True
        try:
            a = self.config["sensors"]["pt"].get("a", 1.0)
            b = self.config["sensors"]["pt"].get("b", 0.0)
            gc.collect()
            while self.spi_lock:
                sleep_ms(100)
            self.spi_lock = True
            spi2.init(phase=1)
            tmp = pt.temperature
            print(f'pt100: {tmp}') 
            self.spi_lock = False
            self.thread_lock.acquire()
            self.data['pt']['raw'] = round(tmp, 2)
            self.data['pt']['scaled'] = round(a * tmp + b, 2)
            self.thread_lock.release()
            
        except Exception as e:
            print(f"Exception from pt100 handle:")
            print_exception(e)
        self.last_pt100_update = time()
        self.pt100_update_running = False
    
    def update_ais(self):
        self.ais_update_running = True
        
        try:
            for sensor in ['a1', 'a2', 'a3', 'c1', 'c2']:
                # wdt.feed()
                if self.config['sensors'][sensor]['en']:
                    a = self.config["sensors"][sensor].get("a", 1.0)
                    b = self.config["sensors"][sensor].get("b", 0.0)
                    temp = 0
                    for _ in range(10):
                        temp += AIs[sensor].read_u16()
                        sleep_ms(10)
                    temp /= 10
                    self.thread_lock.acquire()
                    self.data[sensor]['raw'] = round(temp, 2)                
                    self.data[sensor]['scaled'] = round(a * temp + b, 2)
                    self.thread_lock.release()
        except Exception as e:
            print(f"Exception from AIs handle:")
            print_exception(e)
        self.last_ais_update = time()
        self.ais_update_running = False
            
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
            self.data['sd_warning'] = 1
            return
        # wdt.feed()
        ls = os.listdir('/')
        if 'sd' not in ls:
            print('SD not mounted, trying to mount sd...', end='')
            try:
                os.mount(sd, '/sd')
                print('Done')
            except:
                print('failed to mount sd')
                self.sd_available = False
                self.spi_lock = False
                self.data['sd_warning'] = 1
                return
        # wdt.feed()
        ls = os.listdir('/sd')
        if 'data' not in ls:
            print('Creating "data" directory...', end='')
            try:
                os.mkdir('/sd/data')
                print('Done')
            except:
                print('failed')
                self.sd_available = False
                self.spi_lock = False
                self.data['sd_warning'] = 1
                return
        # wdt.feed()
        ls = os.listdir('/sd/data')
        if 'raw' not in ls:
            print('Creating "raw" directory...', end='')
            try:
                os.mkdir('/sd/data/raw')
                print('Done')
            except:
                print('failed')
                self.sd_available = False
                self.spi_lock = False
                self.data['sd_warning'] = 1
                return
        # wdt.feed()
        if 'scaled' not in ls:
            print('Creating "scaled" directory...', end='')
            try:
                os.mkdir('/sd/data/scaled')
                print('Done')
            except:
                print('failed')
                self.sd_available = False
                self.spi_lock = False
                self.data['sd_warning'] = 1
                return
        self.spi_lock = False

    def log_data(self):
        self.sd_log_running = True
        try:
            gc.collect()
            while self.spi_lock:
                sleep_ms(100)
            self.spi_lock = True
            spi2.init(phase=0)
            # wdt.feed()
            tm = rtc.datetime()
            raw_filename = f'/sd/data/raw/{tm[0]}-{tm[1]:02d}-{tm[2]:02d}.csv'
            scaled_filename = f'/sd/data/scaled/{tm[0]}-{tm[1]:02d}-{tm[2]:02d}.csv'
            print('Log data to sdcard')
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

            raw_file.write('\r\n')
            scaled_file.write('\r\n')
            raw_file.close()
            scaled_file.close()
            self.spi_lock = False
        except Exception as e:
            print(f"Exception from log_data:")
            print_exception(e)
            self.spi_lock = False
        self.last_sd_log = time()
        self.sd_log_running = False
    
    def generate_data_sms(self):
        tm = rtc.datetime()
        data_sms = f'{tm[0]},{tm[1]:02d},{tm[2]:02d},{tm[4]:02d}'
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
        if dst == 'sim800':
            print('switch uart to sim800')
            if self.uart_tx == _sim800_tx and self.uart_rx == _sim800_rx:
                print('already on sim800')
                return
            machine.Pin(self.uart_tx, machine.Pin.OUT, value=1)
            machine.Pin(self.uart_rx, machine.Pin.OUT, value=1)
            uart.init(115200, tx=_sim800_tx, rx=_sim800_rx)
            self.uart_tx = _sim800_tx
            self.uart_rx = _sim800_rx
            print('switched to sim800', self.uart_tx, self.uart_rx)
            uart.read()

        elif dst == 'rs485':
            print('switch to rs485')
            if self.uart_tx == _rs485_tx and self.uart_rx == _rs485_rx:
                print('already on rs485')
                return
            machine.Pin(self.uart_tx, machine.Pin.OUT, value=1)
            machine.Pin(self.uart_rx, machine.Pin.OUT, value=1)
            uart.init(115200, tx=_rs485_tx, rx=_rs485_rx)
            self.uart_tx = _rs485_tx
            self.uart_rx = _rs485_rx
            print('switched to rs485', self.uart_tx, self.uart_rx)
            uart.read()
            
    def init_modem(self):
        try:
            print('check modem')
            # wdt.feed()
            modem.check_reg()
            print('modem is initialized')
            return True
        except:
            try:
                print('initializing modem')
                # wdt.feed()
                modem.initialize()
                return True
            except:
                print('failed to initialize modem')
                return False
        
    def get_time(self):
        # wdt.feed()
        self.get_time_running = True
        while self.uart_lock:
            sleep_ms(100)
        self.uart_lock = True
        self.switch_uart_to('sim800')

        if not self.init_modem():
            self.last_get_time = time()
            self.get_time_running = False
            self.uart_lock = False
            return
        
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
                    break
            except Exception as e:
                print("Exception from get_time:")
                print_exception(e)
        
        self.get_time_running = False
        self.uart_lock = False
        self.last_get_time = time()
        
    def check_for_sms(self):
        self.sms_check_running = True
        print('checking sms command...')
        while self.uart_lock:
            sleep_ms(100)
        self.uart_lock = True
        self.switch_uart_to('sim800')
        
        if not self.init_modem():
            self.last_sms_check = time()
            self.sms_check_running = False
            self.uart_lock = False
            return
                
        for i in range(1, 16):
            # wdt.feed()
            try:
                number, msg = modem.read_sms(i)
            except:
                self.uart_lock = False
                self.sms_check_running = False
                self.last_sms_check = time()
                return
            modem.delete_sms(i)
            
            if '#stat' in msg:
                print('stat sms received')
                sms_data = self.generate_data_sms()
                modem.send_sms(number, sms_data)
                
            elif '#gp' in msg:
                print('post sms received')
                self.post_data()
            
            elif '#qu' in msg:
                print('csq sms received')
                csq = modem.get_signal_strength()
                modem.send_sms(number, f'{csq}')
                
            elif '#reset' in msg:
                print('reset sms received')
                sleep(1)
                machine.reset()
                
            elif '#update' in msg:
                print('update sms received')
                self.update()
            
            elif '#zero' in msg:
                print('zero percip sms received')
                self.zero_db()
            
            elif '#balance' in msg:
                print('check balance sms received')
                modem.ussd_code("*555*4*3*2#")
                result = modem.ussd_code("*555*1*2#")
                modem.send_sms(number, result)
            elif '007B0022006C006100740022' in msg:
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
                    self.data['location'] = {'lat':loc['lat'], 'lon':loc['lon']}
                except Exception as e:
                    print('Failed parsing gps sms')
                    print_exception(e)
                
                
        self.last_sms_check = time()
        self.sms_check_running = False
        self.uart_lock = False
                
    def post_data(self):
        self.data_post_running = True
        print('Post data')
        if not self.config['gprs']['url']:
            self.last_data_post = time()
            self.data_post_running = False
            print('no url set')
            return
        
        self.data['timestamp'] = time() + 946672200
        unenc_data = bytearray(json.dumps(self.data))
#         print(f'not encrypted data: {unenc_data}')
        # wdt.feed()
        key = ubinascii.unhexlify(self.config['enc']['key'])
        iv = mpyaes.generate_IV(16)
        aes = mpyaes.new(key, mpyaes.MODE_CBC, iv)
        aes.encrypt(unenc_data)
#         print(f'encrypted data: {unenc_data}')
        
        unenc_data = ubinascii.hexlify(iv + unenc_data)
        
        while self.uart_lock:
            sleep_ms(100)
        self.uart_lock = True
        self.switch_uart_to('sim800')
        
        if not self.init_modem():
            self.last_data_post = time()
            self.data_post_running = False
            self.uart_lock = False
            return
        for _ in range(3):
            try:
                # wdt.feed()
                modem.connect(self.config['gprs']['apn'])
                # wdt.feed()
                result = modem.http_request(self.config['gprs']['url'], mode='POST', data=f'data={unenc_data.decode()}', content_type='application/x-www-form-urlencoded')
                print(result.status_code)
                # wdt.feed()
                modem.disconnect()
                if result.status_code == 200:
                    break
            except Exception as e:
                print("Exception from post_data:")
                print_exception(e)
                
        self.last_data_post = time()
        self.data_post_running = False
        self.uart_lock = False
        
    def send_data_sms(self):
        self.data_sms_running = True
        
        while self.uart_lock:
            sleep_ms(100)
        self.uart_lock = True
        self.switch_uart_to('sim800')

        if not self.init_modem():
            self.last_data_sms = time()
            self.data_sms_running = False
            self.uart_lock = False
            return
        
        data = self.generate_data_sms()
        # wdt.feed()
        if self.config['sms']['phone_1']:
            modem.send_sms(self.config['sms']['phone_1'], data)
        # wdt.feed()
        sleep(1)
        if self.config['sms']['phone_2']:
            modem.send_sms(self.config['sms']['phone_2'], data)
        
        self.data_sms_running = False
        self.uart_lock = False
        self.last_data_sms = time()
    
    def get_data_str(self):
        s = ''
        for sensor in self.data:
            s += f'{sensor}: {self.data[sensor]}\r\n'
        return s
    
    def check_update(self):
        self.update_check_running = True
        print('check update')
        
        while self.uart_lock:
            sleep_ms(100)
        self.uart_lock = True
        self.switch_uart_to('sim800')
        
        if not self.init_modem():
            self.last_update_check = time()
            self.update_check_running = False
            self.uart_lock = False
            return
        
        for _ in range(3):
            try:
                # wdt.feed()
                modem.connect(self.config['gprs']['apn'])
                # wdt.feed()
                result = modem.http_request('http://fw.abfascada.ir/ahv_rtu2/version.php')
                print(result.status_code)
                try:
                    new_version = float(result.content)
                except:
                    new_version = 0
                if new_version <= _firmware_version:
                    print('no update found')
                    modem.disconnect()
                    break
                print(f'new version found: {new_version}')
                # wdt.feed()
                result = modem.download(f'http://fw.abfascada.ir/ahv_rtu2/main_{new_version}.bin', f'main_{new_version}.py')
                # wdt.feed()
                modem.disconnect()
                if result.status_code == 200:
                    update_info = {'old_version':_firmware_version,
                                   'new_version':new_version}
                    with open('update.json', 'w') as f:
                        json.dump(update_info, f)
                    print('restarting to apply update')
                    sleep(1)
                    machine.reset()
                    break
            except Exception as e:
                print("Exception from post_data:")
                print_exception(e)
                
        self.last_update_check = time()
        self.update_check_running = False
        self.uart_lock = False
        
    def send_loc_request_sms(self):
        print('send location request sms')
        
        while self.uart_lock:
            sleep_ms(100)
        self.uart_lock = True
        self.switch_uart_to('sim800')
        
        if not self.init_modem():
            self.uart_lock = False
            return
        try:
            # wdt.feed()
            cell_info = modem.get_eng_data()
            # wdt.feed()
            modem.send_sms("30004505003188", json.dumps(cell_info))
        except Exception as e:
            print('Exception from send loc request sms:')
            print_exception(e)
            
        self.uart_lock = False
    
    def loop(self):
        while True:
            try:
                # wdt.feed()
                gc.collect()
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
                
                if not self.sd_log_running and time() - self.last_sd_log > int(self.config['log']['interval']):
                    _thread.start_new_thread(self.log_data, ())
                    
                if not self.sms_check_running and time() - self.last_sms_check > _sms_check_interval:
                    _thread.start_new_thread(self.check_for_sms, ())
                
                if self.config['gprs']['url']:
                    if not self.data_post_running and time() - self.last_data_post > int(self.config['gprs']['interval']):
                        _thread.start_new_thread(self.post_data, ())
                        
                if self.config['sms']['phone_1'] or self.config['sms']['phone_2']:
                    if not self.data_sms_running and time() - self.last_data_sms > int(self.config['sms']['interval']):
                        _thread.start_new_thread(self.send_data_sms, ())
                        
                if time() - self.last_loc_request > _loc_request_interval:
                    _thread.start_new_thread(self.send_loc_request_sms, ())
                    self.last_loc_request = time()
                    
                if not self.sms_time_set:
                    if time() - self.last_get_time > _get_time_interval:
                        _thread.start_new_thread(self.get_time, ())
                        
                # wdt.feed()
                sleep(5)
                while self.uart_lock:
                    sleep_ms(100)
                rs_ctl.value(1)
                self.uart_lock = True
                self.switch_uart_to('rs485')
                uart.write(self.get_data_str())
                sleep_ms(100)
                rs_ctl.value(0)
                self.uart_lock = False
                
            except Exception as e:
                print("Exception from main loop:")
                print_exception(e)
                sleep(1)
# wdt.feed()
print_reset_cause()
print(f'firmware version: {_firmware_version}')
gc.collect()
_thread.start_new_thread(app.run, (), {'debug':True, 'port':80})
sleep(1)
gc.collect()
# wdt.feed()
main_app = App()
main_app.init_sd()
main_app.config = load_config()
# wdt.feed()
# main_app.get_time()
main_app.init_sensors()
main_app.init_percip_db()
main_app.loop()