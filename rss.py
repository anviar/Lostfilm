import requests
import os
import re
import json
import argparse
import logging
from yaml import load as yaml_load
from xml.etree import ElementTree

# Парсинг настроек
with open(os.path.join(os.path.dirname(os.path.realpath(__file__)), 'config.yml'), 'r') as yaml_config:
    config = yaml_load(yaml_config)

# Подготовка аргументов командной строки
argparser = argparse.ArgumentParser(description='Загрузить обновления с Lostfilm.TV RSS ленты')
argparser.add_argument('--log-level', help='debug level', type=str,
                       choices=['debug', 'info', 'warning', 'error'], default='error')
argparser.add_argument('--log-save', help='save log file', action="store_true")
args = argparser.parse_args()

# настройка логирования
if args.log_save:
    logging.basicConfig(
        filename=os.path.join(os.path.dirname(os.path.realpath(__file__)), 'rss.log'),
        format='%(asctime)s %(message)s',
        level=args.log_level.upper()
    )
else:
    logging.basicConfig(level=args.log_level.upper())

# Cookie для авторизации на трекере
cookies = ';'.join([cookie + '=' + str(config['auth'][cookie]) for cookie in config['auth']])

# Строка подключения к transmission RPC
transmission_url = 'http://' + str(config['transmission']['host']) + ':' + str(config['transmission']['port']) \
                   + '/transmission/rpc/'

# Функция запроса transmission RPC
transmission_session_id = None
def transmission_rpc_request(rpc_data) -> json:
    global transmission_session_id
    for counter in range(0,2):
        torrent_request = requests.post(
            transmission_url,
            data=rpc_data,
            headers={'X-Transmission-Session-Id': transmission_session_id},
            auth=(config['transmission']['user'], config['transmission']['password'])
        )
        if torrent_request.status_code == 200:
            break
        elif torrent_request.status_code == 401:
            logging.error('Не авторизован в Transmission')
            exit(1)
        torrent_session_search = re.search('X-Transmission-Session-Id: .+?(?=<)', torrent_request.text)
        if torrent_session_search:
            transmission_session_id = torrent_session_search.group(0).split(':')[1].strip()
    if torrent_request.status_code != 200:
        logging.error('Не Удаётся выполнть запрос к Transmission')
        exit(1)
    return json.loads(torrent_request.text)

# Запрос директории загрузки по-умолчанию
request_download_root = transmission_rpc_request(
    json.dumps({
        'arguments': {
            'fields': ['download-dir']
        },
        'method': 'session-get'
    })
)
if request_download_root['result'] == 'success':
    download_root = request_download_root['arguments']['download-dir']
    logging.debug("Директория: " + str(download_root))
else:
    logging.error('Не могу отправить запрос к transmission')
    exit(1)

# Формируем каталог уже загруженных файлов
request_available_torrents = transmission_rpc_request(
    json.dumps({
        'arguments': {
            'fields': ['name']
        },
        'method': 'torrent-get'
    })
)
if request_available_torrents['result'] == 'success':
    catalog = dict()
    for job in request_available_torrents['arguments']['torrents']:
        if 'LostFilm' not in job['name']:
            continue
        data = job['name'].split('.rus.LostFilm.TV.')[0]
        quality = data.split('.')[-1]
        series = data.split('.')[-2]
        name = data.replace(quality, '').replace(series, '').strip('.').replace('.', ' ')
        # Обработка нестандартного именования серий
        if name in config['aliases']:
            name = config['aliases'][name]
        if name not in catalog:
            catalog.update({name: {series}})
        else:
            catalog[name].add(series)
else:
    logging.error('Не могу отправить запрос к transmission')
    exit(1)
logging.debug("Каталог: " + str(catalog))

# Запрос RSS ленты
list_request = requests.get(config['url'])
list_request.encoding = 'utf-8'
rss_items = ElementTree.fromstring(list_request.text).find('channel').findall('item')

for item in rss_items:
    title = item.find('title').text
    link = item.find('link').text

    search_real_name = re.search("(?!S[0-9]+E[0-9]+)(\([a-zA-Z0-9. ']+\))", title)
    if search_real_name:
        real_name = search_real_name.group(0).strip('()')
        if real_name not in config['subscriptions']:
            logging.debug("Не подписан <%s>: %s " % (real_name, title, ))
            continue
    else:
        logging.warning("Не получилось найти имя: " + title)
        continue

    search_quality = re.search("\[.+\]", title)
    if search_quality:
        quality = search_quality.group(0).strip('[]')
        if quality != config['subscriptions'][real_name]:
            logging.debug("Не то качество <%s>: %s" % (quality, title, ))
            continue
    else:
        logging.warning("Не смог определить качество: " + title)
        continue

    search_series = re.search("\(S[0-9]+E[0-9]+\)", title)
    if search_series:
        series = search_series.group(0).strip('()')
    else:
        logging.warning("Не смог найти серию: " + title)
        continue
    if series.endswith('E99'):
        logging.warning("Сезон: " + title)
        continue
    logging.debug("Имя: \"%s\" Серия: \"%s\" Качество: \"%s\"" % (real_name, series, quality,))
    if real_name in catalog and series in catalog[real_name]:
        logging.debug("Уже добавлено: " + title)
        continue

    # Если удовлетворяет всем условиям, то добавляем в очередь загрузки
    torrent_rpc = json.dumps({
        'arguments': {
            'cookies': cookies,
            'filename': link,
            'download-dir': os.path.join(download_root, real_name.strip('.')) # Имя директории не может оканьчиваться точкой
        },
        'method': 'torrent-add'
    })
    transmission_rpc_request(torrent_rpc)
    logging.info("Добавлено " + title)
