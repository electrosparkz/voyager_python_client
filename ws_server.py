import os
import io
import json
import base64
import logging

from PIL import Image

from voyager_api import VoyagerClient, setup_logging
from websocket_server import WebsocketServer

log = logging.getLogger(__name__)

setup_logging(True, False)

vclient = VoyagerClient('172.16.50.50', 5950)
vclient.start()


def _map_ccdstat(val):
    return {
        0: 'INIT',
        1: 'UNDEF',
        2: 'NO COOLER',
        3: 'OFF',
        4: 'COOLING',
        5: 'COOLED',
        6: 'TIMEOUT COOLING',
        7: 'WARMUP RUNNING',
        8: 'WARMUP END',
        9: 'ERROR'
    }.get(val)


def _map_voystat(val):
    return {
        0: 'STOPPED',
        1: 'IDLE',
        2: 'RUN',
        3: 'ERROR',
        4: 'UNDEFINED',
        5: 'WARNING'
    }.get(val)


def _map_guidestat(val):
    return {
        0: 'STOPPED',
        1: 'WAITING_SETTLE',
        2: 'RUNNING',
        3: 'TIMEOUT_SETTLE'
    }.get(val)


def handle_control_data(message, *args, **kwargs):
    ws_server = kwargs.get('server')
    if not ws_server:
        return

    datastruct = {
        'mount': {
            'alt': message['MNTALT'],
            'az': message['MNTAZ'],
            'conn': message['MNTCONN'],
            'dec': message['MNTDEC'],
            'ra': message['MNTRA'],
            'slew': message['MNTSLEW'],
            'track': message['MNTTRACK'],
            'park': message['MNTPARK'],
            'pier': message['MNTPIER'],
            'meridian': message['MNTTFLIP']
        },
        'setup': {
            'conn': message['SETUPCONN'],
            'ds': message['RUNDS'],
            'seq': message['RUNSEQ'],
            'voyager': _map_voystat(message['VOYSTAT'])

        },
        'sequence': {
            'end': message['SEQEND'],
            'remain': message['SEQREMAIN'],
            'start': message['SEQSTART'],
            'name': message['SEQNAME']
        },
        'camera': {
            'conn': message['CCDCONN'],
            'cooling': message['CCDCOOL'],
            'coolpower': message['CCDPOW'],
            'coolset': message['CCDSETP'],
            'status': _map_ccdstat(message['CCDSTAT']),
            'temp': message['CCDTEMP']
        },
        'focuser': {
            'conn': message['AFCONN'],
            'pos': message['AFPOS'],
            'temp': message['AFTEMP']
        },
        'guider': {
            'conn': message['GUIDECONN'],
            'status': _map_guidestat(message['GUIDESTAT']),
            'x': message['GUIDEX'],
            'y': message['GUIDEY'],
        },

    }

    ws_server.send_message_to_all(json.dumps(datastruct))


def _resize_jpg_image(jpeg_base64, x_orig_size, y_orig_size, scale_factor):
    buffer = io.BytesIO()
    imgdata = base64.b64decode(jpeg_base64)
    img = Image.open(io.BytesIO(imgdata))
    new_img = img.resize((int(x_orig_size / scale_factor), int(y_orig_size / scale_factor)))
    new_img.save(buffer, format="JPEG")
    img_b64 = base64.b64encode(buffer.getvalue())
    return str(img_b64)[2:-1]


def handle_new_jpg(message, *args, **kwargs):
    ws_server = kwargs.get('server')
    if not ws_server:
        return

    scale_factor = 18

    datastruct = {
        'jpgshot': {
            'file': message['File'],
            'target': message['SequenceTarget'],
            'saved': message['TimeInfo'],
            'exposure': message['Expo'],
            'binning': message['Bin'],
            'filter': message['Filter'],
            'hfd': message['HFD'],
            'starindex': message['StarIndex'],
            'x_size': int(message['PixelDimX'] / scale_factor),
            'y_size': int(message['PixelDimY'] / scale_factor),
            'base64data': _resize_jpg_image(message['Base64Data'],
                                            message['PixelDimX'],
                                            message['PixelDimY'],
                                            scale_factor)
        }
    }

    ws_server.send_message_to_all(json.dumps(datastruct))


def _map_shotstat(val):
    return {
        0: 'IDLE',
        1: 'EXPOSE',
        2: 'DOWNLOAD',
        3: 'WAIT_JPG',
        4: 'ERROR'
    }.get(val)


def handle_shot_running(message, *args, **kwargs):
    ws_server = kwargs.get('server')
    if not ws_server:
        return

    datastruct = {
        'shot': {
            'file': message['File'],
            'exposure': message['Expo'],
            'elapsed': message['Elapsed'],
            'elapsedpct': message['ElapsedPerc'],
            'status': _map_shotstat(message['Status'])
        }
    }

    ws_server.send_message_to_all(json.dumps(datastruct))


vclient.cmd.set_dashboard('enable')

def new_client(client, server):
    log.info("New client connected and was given id %d" % client['id'])


def client_left(client, server):
    log.info("Client(%d) disconnected" % client['id'])

PORT=9001
server = WebsocketServer(port=PORT)
server.set_fn_new_client(new_client)

vclient.add_handler('ControlData', handle_control_data, server=server)
vclient.add_handler('NewJPGReady', handle_new_jpg, server=server)
vclient.add_handler('ShotRunning', handle_shot_running, server=server)

server.run_forever()
