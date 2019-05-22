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
            Path(__file__).resolve().parent / 'rss.log',
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
        logging.error('transmission RPC: {}'.format(torrent_request))
        exit(torrent_request.status_code)
    response = json.loads(torrent_request.text)
    if response['result'] != 'success':
        logging.error('transmission RPC: {}'.format(response))
        exit(1)
    return response


# Запрос директории загрузки по-умолчанию
request_download_root = transmission_rpc_request(
    {
        'arguments': {
            'fields': ['download-dir']
        },
        'method': 'session-get'
    })

download_root = Path(request_download_root['arguments']['download-dir'])
logging.debug("Директория: {}".format(download_root))


# Формируем каталог уже загруженных файлов
request_available_torrents = transmission_rpc_request(
    {
        'arguments': {
            'fields': ['name']
        },
        'method': 'torrent-get'
    }
)

catalog = dict()
for job in request_available_torrents['arguments']['torrents']:
    if 'LostFilm' not in job['name']:
        continue
    if ' - LostFilm.TV' in job['name']:
        name = ' '.join(job['name'].split(' - LostFilm.TV')[0].split()[:-1])
        series = 'S{:02d}E99'.format(int(job['name'].split(' - LostFilm.TV')[0].split()[-1]))
    else:
        data = job['name'].split('.rus.LostFilm.TV.')[0]
        series = data.split('.')[-2]
        quality = data.split('.')[-1]
        name = data.replace(quality, '').replace(series, '').strip('.').replace('.', ' ')
    # Обработка нестандартного именования серий
    if name in config['aliases']:
        name = config['aliases'][name]
    if name not in catalog:
        catalog.update({name: {series}})
    else:
        catalog[name].add(series)
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
    quality = item.find('category').text.strip('[]')

    # Парсинг атрибутов раздачи
    search_real_name = re.search(r"\(.+\)\.", title)
    if search_real_name:
        real_name = search_real_name.group(0).strip('().')
        search_series = re.search(r"\(S[0-9]+E[0-9]+\)", title)
        if search_series:
            series = search_series.group(0).strip('()')
        else:
            logging.warning(f"Не смог найти серию: {title}")
            continue
    else:
        logging.warning(f"Не получилось найти имя: {title}")
        continue

    if (
        (
            # Подписка на сезон
            (
                series.endswith('E99') and
                (
                    (
                        # Качаем все завершённые сезоны
                        isinstance(config['subscriptions_season'], str) and
                        quality == config['subscriptions_season']
                    ) or
                    (
                        # Качаем только нужные сезоны
                        isinstance(config['subscriptions_season'], dict) and
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
        ) and
        real_name not in config['blacklist']
    ):
        logging.info(f"Добавляем {title}")
        logging.debug(f'real_name={real_name}, series={series}, quality={quality}')
        transmission_rpc_request({
            'arguments': {
                'cookies': cookies,
                'filename': link,
                # Имя директории не может оканчиваться точкой
                'download-dir': str(download_root / real_name.strip('.'))
            },
            'method': 'torrent-add'
        })
    else:
        logging.debug(f'Пропуск real_name={real_name}, series={series}, quality={quality}')
