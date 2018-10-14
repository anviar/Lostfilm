# -*- coding: utf-8 -*-
import requests
from pathlib import Path
import re
import json
import logging
from logging.handlers import RotatingFileHandler
from yaml import load as yaml_load
from xml.etree import ElementTree

# Парсинг настроек
with open(Path(__file__).resolve().parent / 'config.yml', 'r') as yaml_config:
            config = yaml_load(yaml_config)

# настройка логирования
logging.basicConfig(
    format='%(asctime)s [%(levelname)s] %(message)s',
    level=config['verbose'].upper(),
    handlers=[
        RotatingFileHandler(
            Path(__file__).resolve().parent / config['log']['path'],
            maxBytes=config['log']['maxBytes'],
            backupCount=config['log']['backupCount']
        ),
    ]
)


# Cookie для авторизации на трекере
cookies = ';'.join(['{}={}'.format(cookie, config['auth'][cookie]) for cookie in config['auth']])

# Строка подключения к transmission RPC
transmission_url = 'http://{host}:{port}/transmission/rpc/'.format(
    host=config['transmission']['host'],
    port=config['transmission']['port'])


# Функция запроса transmission RPC
transmission_session_id = None


def transmission_rpc_request(rpc_data: dict) -> dict:
    global transmission_session_id
    for _ in range(2):
        torrent_request = requests.post(
            transmission_url,
            data=json.dumps(rpc_data),
            headers={'X-Transmission-Session-Id': transmission_session_id},
            auth=(config['transmission']['user'], config['transmission']['password']),
            timeout=config['timeout']
        )
        if torrent_request.status_code == 200:
            break
        elif torrent_request.status_code == 401:
            logging.error('Не авторизован в Transmission')
            exit(401)
        torrent_session_search = re.search('X-Transmission-Session-Id: .+?(?=<)',
                                           torrent_request.text)
        if torrent_session_search:
            transmission_session_id = torrent_session_search.group(0).split(':')[1].strip()
    if torrent_request.status_code != 200:
        logging.error('transmission RPC:{}'.format(torrent_request.status_code))
        exit(torrent_request.status_code)
    return json.loads(torrent_request.text)


# Запрос директории загрузки по-умолчанию
request_download_root = transmission_rpc_request(
    {
        'arguments': {
            'fields': ['download-dir']
        },
        'method': 'session-get'
    })
if request_download_root['result'] == 'success':
    download_root = Path(request_download_root['arguments']['download-dir'])
    logging.debug("Директория: {}".format(download_root))
else:
    logging.error('transmission RPC: {}'.format(request_download_root))
    exit(1)

# Формируем каталог уже загруженных файлов
request_available_torrents = transmission_rpc_request(
    {
        'arguments': {
            'fields': ['name']
        },
        'method': 'torrent-get'
    }
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
    logging.error('Не удалось отправить запрос к transmission')
    exit(1)
logging.debug("Каталог: {}".format(catalog))

# Запрос RSS ленты
list_request = requests.get(
    config['url'],
    timeout=config['timeout'])
list_request.encoding = 'utf-8'
rss_items = ElementTree.fromstring(list_request.text).find('channel').findall('item')

for item in rss_items:
    title = item.find('title').text
    link = item.find('link').text

    # Парсинг атрибутов раздачи
    search_real_name = re.search(r"(?!S[0-9]+E[0-9]+)(\([a-zA-Z0-9. ']+\))", title)
    if search_real_name:
        real_name = search_real_name.group(0).strip('()')
        search_quality = re.search(r"\[.+\]", title)
        if search_quality:
            quality = search_quality.group(0).strip('[]')
            search_series = re.search(r"\(S[0-9]+E[0-9]+\)", title)
            if search_series:
                series = search_series.group(0).strip('()')
            else:
                logging.warning("Не смог найти серию: " + title)
                continue
        else:
            logging.warning("Не смог определить качество: " + title)
            continue
    else:
        logging.warning("Не получилось найти имя: " + title)
        continue

    if (
        (
            # Подписка на сезон
            (
                series.endswith('E99') and
                (
                    quality == config['download_all_seasons'] or
                    (
                        real_name in config['subscriptions_season'] and
                        quality == config['subscriptions_season'][real_name]
                    )
                )
            ) or

            # Подписка на каждую серию отдельно
            (
                real_name in config['subscriptions'] and
                quality == config['subscriptions'][real_name]
            )
        ) and not
        # Ещё не добавляли
        (
            real_name in catalog and
            series in catalog[real_name]
        )
    ):
        logging.info("Добавляем " + title)
        torrent_rpc = {
            'arguments': {
                'cookies': cookies,
                'filename': link,
                # Имя директории не может оканчиваться точкой
                'download-dir': str(download_root / real_name.strip('.'))
            },
            'method': 'torrent-add'
        }
        transmission_rpc_request(torrent_rpc)
    else:
        logging.debug('Пропуск real_name={real_name}, series={series}, quality={quality}'.format(
            real_name=real_name,
            series=series,
            quality=quality))
