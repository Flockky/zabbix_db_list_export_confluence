import copy
from pyzabbix.api import ZabbixAPI
from atlassian import Confluence
from html import escape
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib
import urllib3
import os

# ================= CONFIGURATION =================
# Эти значения можно переопределить через переменные окружения, 
# если не хотите хранить их даже в виде констант в коде.

ZABBIX_URL = os.environ.get('ZABBIX_URL', 'https://zabbix.your-domain.ru/')
ZABBIX_API_TOKEN = os.environ.get('ZABBIX_API_TOKEN', 'YOUR_ZABBIX_API_TOKEN')

CONFLUENCE_URL = os.environ.get('CONFLUENCE_URL', 'https://wiki.your-domain.ru/')
CONFLUENCE_USER = os.environ.get('CONFLUENCE_USER', 'SA-Zabbix-Confluence')
# Пароль от Confluence обязательно должен быть в ENV: SA_Zabbix_Confluence_Password

SMTP_SERVER = os.environ.get('SMTP_SERVER', 'smtp.your-domain.ru')
SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
EMAIL_FROM_ADDR = os.environ.get('EMAIL_FROM_ADDR', 'AlertMDC@your-domain.ru')
EMAIL_AUTH_USER = os.environ.get('EMAIL_AUTH_USER', 'SA-MailMDC-Prod')
EMAIL_AUTH_PASS = os.environ.get('EMAIL_AUTH_PASS', 'YOUR_SMTP_PASSWORD')
EMAIL_TO_ERROR = os.environ.get('EMAIL_TO_ERROR', 'MDC@your-domain.ru')

# ID шаблона, который маркирует хост как VIP
VIP_TEMPLATE_ID = '11207'

# Список задач: (GroupID_in_Zabbix, DB_Name_For_Title, Confluence_Page_ID)
TASKS = [
    (233, 'MSSQL', 245666928),
    (15, 'Oracle', 245666926),
    (185, 'PostgreSQL', 245666924),
]
# =================================================

def send_email(addr_to, msg_subj, msg_text):
    msg = MIMEMultipart()
    msg['From'] = EMAIL_FROM_ADDR
    msg['To'] = addr_to
    msg['Subject'] = msg_subj
    msg.attach(MIMEText(msg_text, 'plain'))

    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        # server.set_debuglevel(True)
        server.login(EMAIL_AUTH_USER, EMAIL_AUTH_PASS)
        server.send_message(msg)
        server.quit()
    except Exception as e:
        print(f"Failed to send email notification: {e}")


def get_host_details(zapi, host_dict):
    """
    Собирает детальную информацию о хосте: IP, группы, шаблоны, версия, VIP статус.
    """
    hostid = host_dict['hostid']
    host_info = {
        'host': host_dict['host'],
        'description': host_dict['description'],
        'status': host_dict['status'],
        'templates': [],
        'is_vip': False,
        'ip': "",
        'groups': [],
        'version': ''
    }

    # 1. Шаблоны и VIP статус
    try:
        host_templates_req = zapi.do_request(method="host.get", params={
            "output": ["hostid"],
            "selectParentTemplates": ["templateid", "name"],
            "hostids": hostid
        })
        
        if host_templates_req['result']:
            for template in host_templates_req['result'][0]['parentTemplates']:
                host_info['templates'].append(template['name'])
                if template['templateid'] == VIP_TEMPLATE_ID:
                    host_info['is_vip'] = True
    except Exception as e:
        print(f"Error getting templates for {hostid}: {e}")

    # 2. Интерфейсы (IP)
    try:
        host_interfaces = zapi.do_request(method="host.get", params={
            "output": ["hostid"],
            "selectInterfaces": "extend",
            "hostids": hostid
        })
        if host_interfaces['result'] and len(host_interfaces['result'][0]['interfaces']) > 0:
            host_info['ip'] = host_interfaces['result'][0]['interfaces'][0]['ip']
    except Exception as e:
        print(f"Error getting interfaces for {hostid}: {e}")

    # 3. Группы
    try:
        host_groups = zapi.do_request(method="host.get", params={
            "output": ["hostid"],
            "selectHostGroups": "extend",
            "hostids": hostid
        })
        if host_groups['result']:
            for group in host_groups['result'][0]['hostgroups']:
                host_info['groups'].append(group['name'])
    except Exception as e:
        print(f"Error getting groups for {hostid}: {e}")

    # 4. Версия СУБД
    try:
        postgres_item = zapi.do_request(method="item.get", params={
            "filter": {"hostid": hostid},
            "search": {"name": "vers"}
        })
        
        itemid = ''
        for item in postgres_item['result']:
            if 'PostgreSQL' in item['name'] or 'Status' in item['name']:
                itemid = item['itemid']
                break 
        
        if itemid:
            history_item = zapi.do_request(method="history.get", params={
                "itemids": [int(itemid)],
                "hostids": [int(hostid)],
                "history": 1, # Text history
                "sortfield": "clock",
                "sortorder": "DESC",
                "limit": 1
            })
            if len(history_item['result']) > 0:
                arr = history_item['result'][0]['value'].split(" ")
                host_info['version'] = " ".join(arr[:2])
    except Exception as e:
        print(f"Error getting version for {hostid}: {e}")

    return host_info


def process(groupid, db, pageid):
    try:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
        zapi = ZabbixAPI(ZABBIX_URL)
        zapi.session.timeout = (10, 120)
        zapi.session.verify = False
        zapi.login(api_token=ZABBIX_API_TOKEN)

        registry = {}
        prefixes_count = {}

        hosts = zapi.do_request(method="host.get", params={
            "groupids": groupid,
        })

        for host_dict in hosts['result']:
            arr = host_dict['host'].split("-")
            prefix = arr[1].upper() if len(arr) > 1 else arr[0].upper()
            
            # Инициализация префикса в реестре
            if prefix not in registry:
                registry[prefix] = {}
                prefixes_count[prefix] = 0
            
            prefixes_count[prefix] += 1
            
            # Получаем детали хоста через вспомогательную функцию
            host_details = get_host_details(zapi, host_dict)
            registry[prefix][host_dict['hostid']] = host_details

        # Инициализация Confluence
        confluence_pass = os.environ.get("SA_Zabbix_Confluence_Password")
        if not confluence_pass:
            raise Exception("Environment variable SA_Zabbix_Confluence_Password is not set")

        confluence = Confluence(
            url=CONFLUENCE_URL,
            username=CONFLUENCE_USER,
            password=confluence_pass,
            verify_ssl=False
        )
        
        # --- Формирование таблицы статистики ---
        stats_by_prefix = {}
        for prefix in registry:
            node_count = 0
            vip_count = 0
            for host_id in registry[prefix]:
                if registry[prefix][host_id].get('is_vip', False):
                    vip_count += 1
                else:
                    node_count += 1
            stats_by_prefix[prefix] = {'nodes': node_count, 'vips': vip_count}

        count = 0
        total_servers = 0
        table_html = '''<b>Данная страница генерируется автоматически. Внесенные изменения не будут сохранены. <br /><br /> Список префиксов АС:<br /></b>
<table border="1">
    <tr>
        <th>№</th>
        <th>Префикс АС</th>
        <th>Количество серверов</th>
        <th>Кол-во нод (без VIP)</th>
        <th>Кол-во VIP (без нод)</th>
    </tr>'''
        
        total_nodes_sum = 0
        total_vips_sum = 0
        
        # Сортируем префиксы для красивого вывода
        sorted_prefixes = sorted(prefixes_count.keys())

        for prefix in sorted_prefixes:
            count += 1
            servers_in_prefix = prefixes_count[prefix]
            total_servers += servers_in_prefix
            
            nodes = stats_by_prefix[prefix]['nodes']
            vips = stats_by_prefix[prefix]['vips']
            
            total_nodes_sum += nodes
            total_vips_sum += vips
            
            table_html += '''
            <tr>
                <td>{}</td>
                <td><b>{}</b></td>
                <td>{}</td>
                <td>{}</td>
                <td>{}</td>
            </tr>'''.format(count, prefix, str(servers_in_prefix), nodes, vips)
        
        table_html += '''
        <tfoot>
            <tr>
              <th colspan="2">Итого :</th>
              <td>{}</td>
              <td>{}</td>
              <td>{}</td>
            </tr>
           </tfoot>
</table><br></br><br></br>'''.format(total_servers, total_nodes_sum, total_vips_sum)

        # --- Формирование детальных таблиц по префиксам ---
        for prefix in sorted_prefixes:
            count = 0
            table_html += '''<table border="1">
                <tr>
                    <th>№</th>
                    <th>Узел сети</th>
                    <th>IP-адрес</th>
                    <th>Версия {}</th>
                    <th>Присоединенные шаблоны</th>
                    <th>Группы</th>
                    <th>Описание</th>
                    <th>Статус</th>
                </tr>'''.format(db)
            
            # Сортируем хосты внутри префикса по имени
            sorted_hosts = sorted(registry[prefix].items(), key=lambda x: x[1]['host'])

            for host_id, h_data in sorted_hosts:
                count += 1
                status_color = '#72fc00' if h_data['status'] == '0' else '#ff8c69'
                status_text = 'Активирован' if h_data['status'] == '0' else 'Деактивирован'
                
                templates_html = '<ul><li>' + '</li><li>'.join(h_data['templates']) + '</li></ul>' if h_data['templates'] else ''
                groups_html = '<ul><li>' + '</li><li>'.join(h_data['groups']) + '</li></ul>' if h_data['groups'] else ''
                desc_clean = escape(str(h_data['description'])).replace('\r\n', '<br></br>') if h_data['description'] else ''

                # Формируем ссылку на хост в Zabbix динамически
                zabbix_host_url = '{}/zabbix.php?action=popup&amp;popup=host.edit&amp;hostid={}'.format(ZABBIX_URL.rstrip('/'), host_id)

                table_html += '''
                <tr>
                    <td>{}</td>
                    <td><a href="{}">{}</a></td>
                    <td>{}</td>
                    <td>{}</td>
                    <td>{}</td>
                    <td>{}</td>
                    <td>{}</td>
                    <th align="center" style="background-color: {}">{}</th>
                </tr>'''.format(
                    escape(str(count)),
                    zabbix_host_url,
                    escape(h_data['host']),
                    escape(str(h_data['ip'])),
                    escape(str(h_data['version'])),
                    templates_html,
                    groups_html,
                    desc_clean,
                    status_color,
                    status_text
                )
            table_html += '</table><br></br><br></br>'

        # Обновление страницы
        confluence.update_page(pageid, "Реестр серверов - " + db, body=table_html)
        print(f"Successfully updated page for {db}")

    except Exception as error:
        error_msg = f"ОШИБКА ВЫПОЛНЕНИЯ СКРИПТА zabbix_db_list\nОшибка - {error}"
        print(error_msg)
        send_email(EMAIL_TO_ERROR, 'ОШИБКА ВЫПОЛНЕНИЯ СКРИПТА zabbix_db_list', error_msg)
        exit(1)


if __name__ == "__main__":
    for group_id, db_name, page_id in TASKS:
        process(group_id, db_name, page_id)
