# PPG Respiratory Estimator

Определяем частоту дыхания по пульсовому датчику, без отдельного датчика дыхания.
Железо: ESP32-S3 и MAX30102, обработка на Python.

## О чём проект

MAX30102 светит красным и инфракрасным светом в палец и измеряет, сколько света
вернулось. Это сигнал фотоплетизмограммы (PPG): на нём видна каждая пульсовая
волна. Дыхание немного меняет этот сигнал, в том числе высоту пульсовых волн.
Если измерить высоту каждой волны и посмотреть, как она растёт и падает со
временем, из этого ритма можно получить частоту дыхания.

Что делает программа:

1. Плата читает датчик (100 отсчётов в секунду) и отправляет данные на компьютер.
2. Python фильтрует сигнал и находит пульсовые волны.
3. Для каждой волны считается размах `A = P - T` (вершина минус впадина перед ней).
4. Ряд `A` приводится к равномерной сетке и фильтруется в диапазоне дыхания
   (0.1-0.5 Гц, то есть 6-30 вдохов в минуту).
5. Частота дыхания берётся по главному пику спектра в окне 32 с.
6. Результат сравнивается с независимым подсчётом вдохов.

Если сигнал плохой, программа не выдаёт число, а пишет причину (`LOW QUALITY`
или `INSUFFICIENT DATA`).

На плате есть ещё экран: если подключить OLED 0.96", на нём показывается пульс и
бегущая кривая, как на больничном мониторе. Этот пульс считается упрощённо и
только для показа.

## Исследовательский вопрос

**Насколько хорошо модуляция размаха пульсовых волн (peak-to-peak) совпадает с
реальным количеством вдохов?**

Совпадение мы смотрим не только по корреляции, но и по ошибке (MAE, RMSE), смещению
и графику Бланда-Альтмана: корреляция может быть высокой, а числа всё равно
неверными.

Эталон дыхания: помощник нажимает кнопку BOOT на плате в начале каждого вдоха.
Нажатия пишутся в тот же поток, что и сигнал, поэтому отдельная синхронизация не
нужна. Подробный протокол измерений: [docs/experiment_protocol.md](docs/experiment_protocol.md).

**Статус.** Измерений на людях с эталоном пока нет, поэтому ответа на вопрос нет.
На симулированных сигналах программа находит заданную частоту, но это проверка кода,
а не точности метода. Версия скетча с OLED-экраном пока не подтверждена на плате
(детектор пульса проверен только на компьютере). Ограничения:
[docs/limitations.md](docs/limitations.md).

## Что нужно

- плата с ESP32-S3
- датчик MAX30102
- OLED 0.96" SSD1306 128x64, I2C (по желанию)
- макетная плата, провода, USB-кабель с передачей данных
- Windows 10 или 11, Python, Arduino IDE

Подключение (OLED подключается параллельно датчику, к тем же GPIO 8 и 9):

| MAX30102 | OLED | ESP32-S3 |
|----------|------|----------|
| VIN | VCC | 3V3 (не 5V) |
| GND | GND | GND |
| SDA | SDA | GPIO 8 |
| SCL | SCL | GPIO 9 |

Схема: [hardware/wiring_diagram/wiring.svg](hardware/wiring_diagram/wiring.svg).
Если у вашей платы I2C на других контактах, измените `PIN_I2C_SDA` и `PIN_I2C_SCL`
в начале скетча.

## Установка на Windows

1. Установите Python 3.12 или 3.13 с [python.org](https://www.python.org/downloads/windows/).
   В установщике отметьте галочку **Add python.exe to PATH**.
2. Скачайте проект: на странице GitHub **Code, Download ZIP**, и распакуйте.
   Или `git clone https://github.com/capitalisming/heart-beat.git`.
3. Дважды щёлкните `install_windows.bat`.

Установщик создаёт папку `.venv`, ставит пакеты (нужен интернет), запускает тесты
и пробный пример на симулированном сигнале. На Windows часть тестов пропускается
(нужен компилятор C++), это нормально.

Установщик на настоящем Windows не проверялся. Если он не сработал, то же самое
вручную в PowerShell, в папке проекта:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest
```

После установки скрипты запускаются через `ppg.bat` (в PowerShell `.\ppg`):

```
ppg scripts\run_estimation.py data\sample\SYNTHETIC_rr12.csv --reference markers
```

Должно получиться `RESPIRATORY RATE ... 12.0 breaths/min`. Файл в `data\sample`
симулированный.

macOS и Linux: `python3 -m venv .venv`, `source .venv/bin/activate`,
`pip install -e ".[dev]"`, дальше `python scripts/...`.

## Прошивка в Arduino IDE

1. Установите [Arduino IDE 2.x](https://www.arduino.cc/en/software).
2. **File, Preferences**, в поле *Additional boards manager URLs* добавьте
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`.
3. **Tools, Board, Boards Manager**: установите *esp32 by Espressif Systems*.
4. **Sketch, Include Library, Manage Libraries**: установите
   *SparkFun MAX3010x Pulse and Proximity Sensor Library*, *Adafruit SSD1306* и
   *Adafruit GFX Library*.
5. Откройте `hardware/firmware/arduino_monitor/arduino_monitor.ino`
   (рядом должен лежать `BeatDetector.h`).
6. В **Tools** выберите плату (например *ESP32S3 Dev Module*) и порт.
   Если Serial Monitor потом пустой, включите **USB CDC On Boot**.
7. Нажмите Upload. Если не загружается: зажать BOOT, нажать RESET, отпустить BOOT.
8. **Tools, Serial Monitor**, скорость 115200.

Положите палец на датчик. На экране появятся пульс и кривая, а в мониторе порта
данные. Если будет ошибка компиляции, сначала проверьте, что все три библиотеки
установлены.

Настройки в начале скетча:

- `SERIAL_MODE`: 0 это данные для записи (по умолчанию), 1 это подписанные
  значения (`IR=... RED=... BPM=...`), 2 это формат для Serial Plotter
- `FINGER_ON` и `FINGER_OFF`: пороги, по которым определяется палец; подберите
  по значениям `IR` без пальца и с пальцем (режим 1)
- `LED_CURRENT`: ток светодиодов, увеличьте, если пульс слабый
- `OLED_ADDRESS`: 0x3C, у некоторых дисплеев 0x3D

Подробная инструкция по-русски с таблицей неполадок:
[docs/arduino_guide_ru.md](docs/arduino_guide_ru.md).

## Запись и анализ

Перед записью закройте Serial Monitor (порт может использовать одна программа) и
поставьте `SERIAL_MODE 0`. Номер порта виден в Arduino IDE или в Диспетчере
устройств (Порты COM и LPT):

```
ppg scripts\record_serial.py --list
ppg scripts\record_serial.py --port COM5 --duration 180 --participant P01 --session S1 --condition rest --analyze
```

Во время записи помощник нажимает BOOT в начале каждого вдоха. После записи
программа показывает частоту дыхания, пульс и эталон по нажатиям, а графики всех
этапов сохраняет в `results\runs\...`.

Для серии записей составьте `data\manifest.csv` (пример: `data\sample\manifest.csv`):

```
ppg scripts\run_evaluation.py data\manifest.csv --outdir results\study --recording-figures
```

В `results\study\report.md` будет отчёт: MAE, RMSE, смещение, корреляции и график
Бланда-Альтмана.

### Формат данных

Плата выдаёт CSV, 100 строк в секунду:

```
sample_index,device_us,ir,red,marker
3334,176979269,52822,58361,0
```

- `sample_index` номер отсчёта (пропуск значит потерю данных)
- `device_us` время платы в микросекундах; по нему программа считает настоящую
  частоту дискретизации
- `ir`, `red` сырой сигнал двух светодиодов
- `marker` равен 1 после нажатия BOOT (отметка вдоха)

Строки с `#` это сообщения платы, программа записи их пропускает.

## Скриншоты

Вывод платы в Serial Monitor (настоящий скриншот с нашей платы):

![Serial Monitor](docs/images/serial_monitor.png)

Здесь `ir` почти не меняется (около 10 отсчётов из 52 000), то есть пульсовой
волны в этом фрагменте нет: нужен плотный контакт пальца с датчиком.

Следующие картинки построены на **симулированном** сигнале (120 с, дыхание
12 в минуту), это не измерение человека.

Найденные пульсовые волны: вершины P, впадины T и размах A = P - T.

![Пульсовые волны](results/synthetic_verification/figures/example_recording/03_pulse_peaks.png)

Дыхательная составляющая, полученная из размаха волн, и отметки вдохов:

![Дыхательный сигнал](results/synthetic_verification/figures/example_recording/06_respiratory_band.png)

Фотографий собранного устройства в репозитории пока нет.

## Референсы

1. Karlen W. et al. Multiparameter respiratory rate estimation from the photoplethysmogram. IEEE Trans Biomed Eng, 2013. doi:10.1109/TBME.2013.2246160
2. Charlton P.H. et al. An assessment of algorithms to estimate respiratory rate from the electrocardiogram and photoplethysmogram. Physiol Meas, 2016. doi:10.1088/0967-3334/37/4/610
3. Charlton P.H. et al. Breathing rate estimation from the electrocardiogram and photoplethysmogram: a review. IEEE Rev Biomed Eng, 2018. doi:10.1109/RBME.2017.2763681
4. Meredith D.J. et al. Photoplethysmographic derivation of respiratory rate: a review of relevant physiology. J Med Eng Technol, 2012. doi:10.3109/03091902.2011.638965
5. Allen J. Photoplethysmography and its application in clinical physiological measurement. Physiol Meas, 2007. doi:10.1088/0967-3334/28/3/R01
6. Pimentel M.A.F. et al. Toward a robust estimation of respiratory rate from pulse oximeters. IEEE Trans Biomed Eng, 2017. doi:10.1109/TBME.2016.2613124
7. Bland J.M., Altman D.G. Statistical methods for assessing agreement between two methods of clinical measurement. Lancet, 1986. doi:10.1016/S0140-6736(86)90837-8
8. Bland J.M., Altman D.G. Agreement between methods of measurement with multiple observations per individual. J Biopharm Stat, 2007. doi:10.1080/10543400701329422
9. Elgendi M. et al. Systolic peak detection in acceleration photoplethysmograms measured from emergency responders in tropical conditions. PLoS ONE, 2013. doi:10.1371/journal.pone.0076585
10. Nilsson L.M. Respiration signals from photoplethysmography. Anesth Analg, 2013. doi:10.1213/ANE.0b013e31828098b2
11. Addison P.S. et al. Developing an algorithm for pulse oximetry derived respiratory rate (RRoxi): a healthy volunteer study. J Clin Monit Comput, 2012. doi:10.1007/s10877-011-9332-y
12. Welch P.D. The use of fast Fourier transform for the estimation of power spectra. IEEE Trans Audio Electroacoust, 1967. doi:10.1109/TAU.1967.1161901

Железо и библиотеки: даташит MAX30102 (Maxim Integrated),
[SparkFun MAX3010x library](https://github.com/sparkfun/SparkFun_MAX3010x_Sensor_Library),
[Adafruit SSD1306](https://github.com/adafruit/Adafruit_SSD1306),
[Adafruit GFX](https://github.com/adafruit/Adafruit-GFX-Library).

Проект учебный, не является медицинским устройством.
