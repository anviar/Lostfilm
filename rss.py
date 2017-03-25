import requests
import os
import re
import json
from yaml import load as yaml_load
from xml.etree import ElementTree

with open(os.path.join(os.path.dirname(os.path.realpath(__file__)),'config.yml'), 'r') as yaml_config:
    config = yaml_load(yaml_config)

cookies=';'.join([cookie + '=' + str(config['auth'][cookie]) for cookie in config['auth']])
transmission_url = 'http://' + str(config['transmission']['host']) + ':' + str(config['transmission']['port']) \
                   + '/transmission/rpc/'

def transmission_rpc_request(data):
    torrent_request = requests.post(transmission_url, data=data)
    if torrent_request.status_code == 409:
        torrent_session_search = re.search('X-Transmission-Session-Id: .+?(?=<)', torrent_request.text)
        if torrent_session_search:
            torrent_request = requests.post(
                transmission_url,
                data=data,
                headers={'X-Transmission-Session-Id': torrent_session_search.group(0).split(':')[1].strip()}
            )
    return (json.loads(torrent_request.text))
request_available_torrents=transmission_rpc_request(
    json.dumps({
            'arguments': {
                'fields': ['name']
            },
            'method': 'torrent-get'
            })
        )
if request_available_torrents.get('result')=='success':
    catalog=dict()
    for job in request_available_torrents.get('arguments').get('torrents'):
        if 'LostFilm' not in job['name']:
            continue
        data=job['name'].split('.rus.LostFilm.TV.')[0]
        quality = data.split('.')[-1]
        series = data.split('.')[-2]
        name = data.replace(quality,'').replace(series,'').strip('.').replace('.',' ')
        if name not in catalog:
            catalog.update({name: [series] })
        else:
            catalog[name].append(series)
else:
    print('Can not send request to transmission')
    exit(1)
list_request = requests.get(config['url'])
list_request.encoding = 'utf-8'
rss_items = ElementTree.fromstring(list_request.text).find('channel').findall('item')

for item in rss_items:
    title = item.find('title').text
    link = item.find('link').text
    search_real_name = re.search("\(.*\)" , title.split('.')[0])
    if search_real_name:
        real_name = search_real_name.group(0).strip('()')
        if real_name not in config['subscriptions']:
            continue
    else:
        continue
    search_quality = re.search("\[.*\]", title.split('.')[1])
    if search_quality:
        quality = search_quality.group(0).strip('[]')
        if quality != config['subscriptions'][real_name]:
             continue
    else:
        continue
    search_series = re.search("\(.*\)" , title.split('.')[1])
    if search_series:
        series = search_series.group(0).strip('()')
    else:
        continue
    if real_name in catalog and series in catalog[real_name]:
        continue
    torrent_rpc = json.dumps({
                            'arguments': {
                                            'cookies': cookies,
                                            'filename': link
                                         },
                            'method': 'torrent-add'
                            })
    transmission_rpc_request(torrent_rpc)
