import time
import json
import uuid
import queue
import socket
import base64
import random
import logging
import threading
import collections

log = logging.getLogger(__name__)


class VoyagerClient(threading.Thread):
    def __init__(self,
                 host,
                 port,
                 client_id=None,
                 group=None,
                 target=None,
                 name=None,
                 args=(),
                 kwargs={}):
        super(VoyagerClient, self).__init__(group=group, target=target, name=name, *args, **kwargs, daemon=True)
        self.host = host
        self.port = port
        self.client_id = client_id

        if not self.client_id:
            self.client_id = random.randrange(1, 10)

        self.cmd_running = None

        self.cmd_assembly = collections.deque()

        self.log_length = 1000
        self.signals_length = 20
        self.messages_length = 30

        self.logs = collections.deque()
        self.signals = collections.deque()
        self.messages = collections.deque()

        self.sock = socket.socket()
        self.sock.settimeout(0.15)

        self.heartbeat_events = [
            'WeatherAndSafetyMonitorData',
            'Version',
            'Polling',
            'ControlData'
        ]

        self._shut_down = threading.Event()
        self._shut_down.clear()

        self._connected = False

        self.handlers = {'Signal': {}}
        self.handler_threads = []

        self.cmd = None

        self.cmd = VoyagerCommandWrapper(self)

    def close(self):
        self._shut_down.set()
        log.info("Waiting for threads to shut down")
        while self._shut_down.is_set():
            time.sleep(0.5)  # Wait for threads

    def _shut_down_handler(self):
        if self._shut_down.is_set():
            log.info("Closing out")
            while self.handler_threads:
                self._thread_cleanup()
            self._send_message(self._encode_message({'method': 'disconnect', "id": self.client_id}))
            self.sock.close()
            self._connected = False
            self._shut_down.clear()
            return True

    def run(self):
        log.info("Connecting")
        self.sock.connect((self.host, self.port))
        while True:
            try:
                data = self.sock.recv(1024).decode()
                break
            except socket.timeout:
                pass
        log.info(f"Version message: {data}")

        while True:
            if self._shut_down_handler():
                break
            self._connected = True
            try:
                data = b''
                while True:
                    try:
                        data_chunk = self.sock.recv(2048)
                        log.debug(f"Main loop receive: {data_chunk}")
                        data += data_chunk
                    except socket.timeout:
                        if not data:
                            raise
                        data = data.decode()
                        break
            except socket.timeout:
                next
            else:
                if not data:
                    log.info("Disconnect")
                    break

                for msg in data.splitlines():
                    dcm = self._decode_message(msg)

                    if dcm and not dcm.get('jsonrpc'):
                        log.debug(f"Got: {dcm}".strip())

                        event = dcm.get('Event', None)

                        if event and event in self.heartbeat_events:
                            self._send_heartbeat()
                            if event not in ('Version', 'Polling'):
                                self._handle_cmd(dcm)
                        elif event == 'Signal':
                            self._handle_signal(dcm)
                        elif event == 'LogEvent':
                            self._handle_log(dcm)
                            self._send_heartbeat()
                        elif event == 'ShutDown':
                            log.warn('Received shutdown signa from host. Closing connection')
                            self.sock.close()
                            return
                        else:
                            self._handle_cmd(dcm)
            self._thread_cleanup()

    def add_handler(self, event_id, callback_func, signal=-1, *args, **kwargs):
        log.info(f"Adding handler for event_id: {event_id}, func: {callback_func}")
        if event_id == 'Signal':
            self.handlers['Signal'][signal] = Handler('Signal', callback_func, signal=signal, *args, **kwargs)
        else:
            self.handlers[event_id] = Handler(event_id, callback_func, *args, **kwargs)

    def remove_handler(self, event_id):
        log.info(f"Removing handler for event_id: {event_id}")
        del self.handlers[event_id]

    def _add_message(self, message):
        log.debug(f"Adding message: {message}")
        if len(self.messages) >= self.messages_length:
            log.debug(f"Message queue full, popped: {self.messages.pop()}")
        self.messages.append(message)

    def _thread_cleanup(self):
        for index, thread in enumerate(self.handler_threads):
            if not thread.is_alive():
                thread.join()
                log.debug(f"Thread {thread.ident} for {thread.handler.handle} shut down")
                del self.handler_threads[index]

    def _handle_signal(self, message):
        message['CodeMsg'] = self.cmd.get_signal(message['Code'])
        log.debug(f"Adding signal: {message}")
        if len(self.signals) >= self.signals_length:
            log.debug(f"Signal queue full, popped: {self.signals.pop()}")
        self.signals.append(message)

        code = message.get('Code', '')
        signal_handler = self.handlers['Signal'].get(code)
        if signal_handler:
            try:
                handler_thread = HandlerThread(message, signal_handler)
                handler_thread.start()
                self.handler_threads.append(handler_thread)
            except:
                pass
            finally:
                return

    def _handle_log(self, message):
        log.debug(f"Adding log: {message}")
        if len(self.logs) >= self.log_length:
            log.debug(f"Log queue full, popped: {self.logs.pop()}")
        self.logs.append(message)

    def _send_heartbeat(self):
        log.debug("Sending heartbeat")
        heartbeat_message = self._encode_message({'Event': 'Polling'})
        self._send_message(heartbeat_message)

    def _handle_cmd(self, message):
        log.debug(f"_handle_cmd input: {message}")
        event = message.get('Event', '')

        cmd_handler = self.handlers.get(event)
        if cmd_handler:
            try:
                handler_thread = HandlerThread(message, cmd_handler)
                handler_thread.start()
                self.handler_threads.append(handler_thread)
            except:
                pass
            finally:
                return

        if self.cmd_running:
            cmd_result = self.cmd_running.lstrip('Remote')
            cmd_result = cmd_result.lstrip('Get')
        else:
            cmd_result = ""

        if event == 'RemoteActionResult' or (self.cmd_running and cmd_result == event):
            self.cmd_assembly.append(message)
            self.cmd_running = None
        elif (self.cmd_running and cmd_result != event) or not self.cmd_running:
            self._add_message(message)

    def get_message(self):
        if len(self.messages) == 0:
            return None
        return self.messages.pop()

    def get_signal(self):
        if len(self.signals) == 0:
            return None
        return self.signals.pop()

    def send_command(self, command, params=None, uid=None):
        log.debug(f"send_command input: {command}, params: {params}")
        while not self._connected:
            time.sleep(0.5)

        if not params:
            params = {}
        if not uid:
            params['UID'] = str(uuid.uuid4())
        else:
            params['UID'] = uid
        params['TimeoutConnect'] = 90

        cmd_assembly = {'method': command, 'params': params, 'id': self.client_id}

        encoded_msg = self._encode_message(cmd_assembly)
        self.cmd_running = command
        self._send_message(encoded_msg)

        while self.cmd_running:
            time.sleep(0.5)

        data_copy = list(self.cmd_assembly)
        self.cmd_assembly.clear()
        data_copy[-1]['ActionResult'] = self.cmd.get_remote_action_result(data_copy[-1]['ActionResultInt'])
        return {'output': data_copy, 'uuid': params['UID']}

    def _send_message(self, encoded_msg):
        log.debug(f"Sending message: {encoded_msg}")
        self.sock.sendall(encoded_msg)

    def _decode_message(self, message):
        log.debug(f"_decode_message input: {message}")
        try:
            decoded_msg = json.loads(message.strip())
            return decoded_msg
        except Exception as e:
            log.warn(f"Decode fail: {message}")
            log.debug(repr(e))
            return None

    def _encode_message(self, message):
        log.debug(f"_encode_message input: {message}")
        try:
            encoded_msg = json.dumps(message) + "\r\n"
            return encoded_msg.encode()
        except Exception as e:
            log.warn(f"Encode fail: {message}")
            log.debug(repr(e))
            return None


class VoyagerCommandWrapper(object):
    def __init__(self, client):
        self._client = client

        self.signal_list = {
            1: 'Autofocus Error',
            2: 'Remote Action RUN - Running Queue is empty',
            3: 'Remote Action RUN - SC ARRAY Autofocus all nodes',
            4: 'Remote Action RUN - Precise Pointing',
            5: 'Remote Action RUN - Autofocus',
            6: 'Remote Action RUN - SC ARRAY AutoFlat single node',
            7: 'Remote Action RUN - SC ARRAY Autofocus single node',
            8: 'Remote Action RUN - SC ARRAY Connect Setup all nodes',
            9: 'Remote Action RUN - SC ARRAY Disconnect Setup all nodes',
            10: 'Remote Action RUN - SC ARRAY Filter Change single node',
            11: 'Remote Action RUN - SC ARRAY Get Actual Filter single node',
            12: 'Remote Action RUN - SC ARRAY Focuser Move To single node',
            13: 'Remote Action RUN - SC ARRAY Focuser Offset single node',
            14: 'Remote Action RUN - SC ARRAY Rotator Move single node',
            15: 'Remote Action RUN - Setup Connect',
            16: 'Remote Action RUN - Setup Disconnect',
            18: 'Remote Action RUN - Camera Shot',
            19: 'Remote Action RUN - CCD Cooling',
            20: 'Remote Action RUN - Focuser Move To',
            21: 'Remote Action RUN - Focuser OffSet',
            22: 'Remote Action RUN - Rotator Goto',
            23: 'Remote Action RUN - AutoFlat',
            24: 'Remote Action RUN - Filter Change To',
            25: 'Remote Action RUN - Plate Solving Actual Location',
            26: 'Remote Action RUN - SC ARRAY Sequence',
            27: 'Remote Action RUN – SC ARRAY Create Directory on FileSystem single node',
            28: 'Remote Action RUN – SC ARRAY CCD Cooling single node',
            29: 'Remote Action RUN - SC ARRAY Get CCD Temperature single node',
            30: 'Remote Action RUN - SC ARRAY Camera Shot single node',
            31: 'Remote Action RUN - Telescope Goto',
            32: 'Remote Action RUN - Run External Script/Application',
            33: 'Remote Action RUN - SC ARRAY AutoFocus all node with LocalField method',
            34: 'Remote Action RUN - SC ARRAY AutoFocus single node with LocalField method',
            500: 'VOYAGER General STATUS - Error (some error from action or thread raised)',
            501: 'VOYAGER General STATUS - Idle (nothing to do ready to work)',
            502: 'VOYAGER General STATUS - Action Running',
            503: 'VOYAGER General STATUS - Action Stopped',
            504: 'VOYAGER General STATUS - Undefined (just started Voyager ... nothing defined)',
            505: 'VOYAGER General STATUS - Warning (some minor error from action or thread raised)',
            506: 'VOYAGER General STATUS - Unknow (Internal Automa cannot understand what asked to Voyager)'
        }

        self.remote_action_results = {
            0: 'NEED INIT',
            1: 'READY',
            2: 'RUNNING',
            3: 'PAUSE',
            4: 'OK',
            5: 'FINISHED ERROR',
            6: 'ABORTING',
            7: 'ABORTED',
            8: 'TIMEOUT',
            9: 'TIME END',
            10: 'OK PARTIAL'
        }

        self.log_level_text = {
            1: {'level': 'DEBUG', 'desc': 'Low level info'},
            2: {'level': 'INFO', 'desc': 'Normal Info'},
            3: {'level': 'WARNING', 'desc': 'Warning info'},
            4: {'level': 'CRITICAL', 'desc': 'Critical info like an error'},
            5: {'level': 'TITLE', 'desc': 'Action running title'},
            6: {'level': 'SUBTITLE', 'desc': 'SubAction running title'},
            7: {'level': 'EVENT', 'desc': 'Event'},
            8: {'level': 'REQUEST', 'desc': 'Command'},
            9: {'level': 'EMERGENCY', 'desc': 'Emergency Management'},
        }

    def get_signal(self, signal):
        return self.signal_list.get(signal)

    def get_remote_action_result(self, action_result):
        if not action_result or action_result not in list(range(0, 11)):
            return
        return self.remote_action_results.get(action_result)

    def get_log_level_text(self, log_level):
        if not log_level or log_level not in list(range(0, 10)):
            return
        return self.log_level_text.get(log_level)

    def set_logs(self, action=None, level=0):
        if not action or action not in ['enable', 'disable']:
            return

        struct = {'IsOn': True if action == 'enable' else False, 'Level': level}
        return self._client.send_command('RemoteSetLogEvent', struct)

    def set_dashboard(self, action=None):
        if not action or action not in ['enable', 'disable']:
            return

        struct = {'IsOn': True if action == 'enable' else False}
        return self._client.send_command('RemoteSetDashboardMode', struct)

    def get_array_element_data(self):
        return self._client.send_command('GetArrayElementData')

    def abort_action(self, uid):
        return self._client.send_command('RemoteActionAbort', uid=uuid)

    def get_filter(self):
        return self._client.send_command('RemoteFilterGetActual')

    def get_filter_configuration(self):
        return self._client.send_command('RemoteGetFilterConfiguration')

    def get_ccd_temp(self):
        return self._client.send_command('RemoteGetCCDTemperature')

    def disconnect_setup(self):
        return self._client.send_command('RemoteSetupDisconnect')

    def connect_setup(self):
        return self._client.send_command('RemoteSetupConnect')

    def precise_point_target(self,
                             ra=0,
                             dec=0,
                             ra_text='',
                             dec_text=''):

        if ra_text and dec_text:
            is_text = True
        elif ra and dec:
            is_text = False

        struct = {
            'IsText': is_text,
            'RA': ra,
            'DEC': dec,
            'RAText': ra_text,
            'DECText': dec_text,
            'Parallelized': False
        }

        return self._client.send_command('RemotePrecisePointTarget', struct)

    def mount_action(self, action):
        actions = {
            'track': 1,
            'no_track': 2,
            'park': 3,
            'unpark': 4,
            'goto_zenith': 5,
            'home': 6
        }

        if not action or action not in actions.keys():
            return
        struct = {'CommandType': actions[action]}

        return self._client.send_command('RemoteMountFastCommand', struct)

    def set_profile(self, profile_filename):
        return self._client.send_command('RemoteSetProfile', {'FileName': profile_filename})


class Handler(object):
    def __init__(self, handle, callback_func, signal=-1, *args, **kwargs):
        self.handle = handle
        self.callback_func = callback_func
        self.signal = signal
        self.args = args
        self.kwargs = kwargs

        log.error(self.__dict__)


class HandlerThread(threading.Thread):
    def __init__(self,
                 message,
                 handler,
                 group=None,
                 target=None,
                 name=None,
                 args=(),
                 kwargs={}):
        super(HandlerThread, self).__init__(group=group, target=target, name=name, *args, **kwargs, daemon=True)

        print(f"Handler thread created for {message} : {handler}")

        self.message = message
        self.handler = handler

    def run(self):
        try:
            log.debug(f"[HandlerThread] Executing thread for handler: {self.handler.handle}, {self.handler.callback_func}")
            self.handler.callback_func(self.message, *self.handler.args, **self.handler.kwargs)
            log.debug(f"[HandlerThread] Executed thread for handler: {self.handler.handle}, {self.handler.callback_func}")            
        except Exception as e:
            log.error(f"[HandlerThread] Failed to execute: {repr(e)}")
        return


def setup_logging(write_file=False, write_console=True):
    log.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - [%(threadName)s:%(thread)d] (%(name)s): [%(levelname)s] %(message)s')
    if write_file:
        fh = logging.FileHandler('voyager_client.log')
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(formatter)
        log.addHandler(fh)

    if write_console:
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(formatter)
        log.addHandler(ch)


def print_jpg_info(message):
    print("***\n***\n***\n***\n***")
    raw_image_base64 = message.get('Base64Data')
    message['Base64Data'] = "<stripped>"
    print(message)
    
    if raw_image_base64:
        raw_image_bytes = raw_image_base64.encode('utf-8')
        with open('image.jpg', 'wb') as file_out:
            decoded_image_data = base64.decodebytes(raw_image_bytes)
            file_out.write(decoded_image_data)
            print("New file saved")
    print("***\n***\n***\n***\n***")


if __name__ == "__main__":
    setup_logging(True)

    client = VoyagerClient('172.16.50.50', 5950)
    client.start()

    client.add_handler('NewJPGReady', print_jpg_info)

    client.cmd.set_logs('enable')
    client.cmd.set_dashboard('enable')

