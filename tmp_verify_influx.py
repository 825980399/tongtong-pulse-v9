import os
from influxdb_client import InfluxDBClient

token = os.environ.get('INFLUXDB_TOKEN', '')
url = os.environ.get('INFLUXDB_URL', 'http://localhost:8086')
org = os.environ.get('INFLUXDB_ORG', 'tongtong')
bucket = os.environ.get('INFLUXDB_BUCKET', 'pulse_metrics')

print(f'URL: {url}')
print(f'ORG: {org}')
print(f'Bucket: {bucket}')
print(f'Token: {token[:20]}...')

client = InfluxDBClient(url=url, token=token, org=org)
health = client.health()
print(f'健康状态: {health.status}')
print(f'版本: {health.version}')
client.close()
print('✅ Python连接验证通过')
