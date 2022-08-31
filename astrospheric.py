import requests
import argparse
import time
from pprint import pprint

lat = 0.0
lon = 0.0
api_key = ""
api_url = "https://astrosphericpublicaccess.azurewebsites.net/api/GetForecastData_V1"

parser = argparse.ArgumentParser()
parser.add_argument("-o", "--offset", type=int, nargs='?', default=0)
parser.add_argument("-d", "--duration", type=int, nargs='?', default=9)
parser.add_argument("-l", "--log", action="store_true", default=False)

args = parser.parse_args()

offset = args.offset
hours_to_cover = args.duration

trans_limit = 16.5
seeing_limit = 3
cloud_cover_limit = 30

request_data = {
    "Latitude": lat,
    "Longitude": lon,
    "MSSinceEpoch": time.time(),
    "APIKey": api_key
}

response = requests.post(api_url, json=request_data)
response.encoding = "utf-8-sig"

current_data = response.json()

seeing = current_data['Astrospheric_Seeing']
trans = current_data['Astrospheric_Transparency']

cloud_cover = current_data['RDPS_CloudCover']

next_seeing = next_trans = next_cloud = 0

for hour in range(0 + offset, hours_to_cover + offset):
    next_seeing += seeing[hour]['Value']['ActualValue']
    next_trans += trans[hour]['Value']['ActualValue']
    next_cloud += cloud_cover[hour]['Value']['ActualValue']

next_seeing = next_seeing / hours_to_cover
next_trans = next_trans / hours_to_cover
next_cloud = next_cloud / hours_to_cover

if args.log:
    print(f"seeing: {next_seeing}, trans: {next_trans}, cloud: {next_cloud}")

if next_seeing >= seeing_limit and next_trans <= trans_limit and next_cloud <= cloud_cover_limit:
    print("RUN")
else:
    print("HALT")
