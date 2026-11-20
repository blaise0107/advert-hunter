"""Быстрая проверка парсеров инструкции на текстах из логов."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hunter2 as h
import tasks as t

T1 = ("NQ141 6740 | руб. | Поисковый запрос: | яндекс поиск | Скопировать в буфер | "
      "Ваше задание: | Для выполнения заказа сверните | приложение AdvertApp и откройте | "
      "Huawei AppGallery на вашем | устройстве. В Huawei AppGallery | введите в поиске "
      "яндекс поиск | —1 место | и установите приложение с иконкой: | ок")
T2 = ("NQ1426335 | руб. | Поисковый запрос: | яндекс | Скопировать в буфер | "
      "Ваше задание: | Для выполнения заказа сверните | приложение AdvertApp и откройте | "
      "Xiaomi GetApps на вашем устройстве. | В Xiaomi GetApps введите в поиске | "
      "яндекс | —2 место | и установите приложение с иконкой: | ок")

for name, text in (("1416740", T1), ("1426335", T2)):
    print(name,
          "query=", repr(h.extract_search_query(text)),
          "place=", h.install_place(text),
          "store=", t.detect_store(text))
