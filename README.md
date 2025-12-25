git clone https://github.com/ваш_логин/tpu_event_system.git
cd tpu_event_system

# Собрать образ
docker build -t tpu-system .

# Запустить контейнер
docker run -p 8000:8000 --name tpu-app tpu-system

🌐 Доступ к приложению

После запуска откройте в браузере:
http://localhost:8000
