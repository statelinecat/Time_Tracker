# create_icon.py
from PIL import Image

# Открываем PNG (должен быть 256x256 или больше)
img = Image.open("timetracker.png")
img.save("app.ico", format='ICO', sizes=[(16,16), (32,32), (48,48), (256,256)])