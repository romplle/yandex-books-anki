# Yandex Books Anki

Инструмент для превращения отмеченных слов из Яндекс Книг в Anki-карточки для изучения английской лексики.

Во время чтения вы отмечаете незнакомые английские слова или короткие фразы как публичные цитаты. Проект собирает эти цитаты, нормализует слово для лицевой стороны карточки, генерирует английское определение и пример употребления через GigaChat, добавляет озвучку и импортирует готовые карточки в Anki.

## Что получается на выходе

Карточка Anki содержит поля:

- `Front` - слово или короткая фраза в нормализованной форме;
- `Meaning` - короткое определение на английском;
- `Example` - пример на английском;
- `Sound` - озвучка слова;
- `Sound_Meaning` - озвучка определения;
- `Sound_Example` - озвучка примера;
- `Source` - книга-источник.

Колода создается автоматически:

- deck: `Yandex Books Vocabulary`;
- note type: `YandexBooksVocabulary`;
- tag: `yandex-books`.

## Установка

```powershell
pip install -r requirements.txt
python -m spacy download en_core_web_sm
```

Зависимости проекта:

- `requests` и `beautifulsoup4` - чтение публичных страниц Яндекс Книг;
- `spacy` + `en_core_web_sm` - лемматизация `Front`;
- `gigachat` - генерация `Meaning` и `Example`;
- `edge-tts` - генерация mp3-аудио.

## Настройка GigaChat

Скопируйте пример настроек:

```bash
copy .env.example .env
```

Затем заполните `GIGACHAT_CREDENTIALS` в `.env`:

```env
GIGACHAT_CREDENTIALS=ваш_ключ_авторизации
GIGACHAT_SCOPE=GIGACHAT_API_PERS
GIGACHAT_MODEL=GigaChat-2
GIGACHAT_VERIFY_SSL_CERTS=false
```

Обязательная переменная только одна:

```env
GIGACHAT_CREDENTIALS=...
```

Остальные значения уже используются по умолчанию.

## Настройка Anki

1. Установите Anki Desktop.
2. Установите add-on AnkiConnect.
3. Запустите Anki перед командой импорта.

Программа обращается к AnkiConnect по адресу:

```text
http://localhost:8765
```

Если Anki закрыт или AnkiConnect не установлен, команда `import` завершится ошибкой `AnkiConnect is unavailable`.

## Основной workflow

### 1. Собрать новые цитаты

Передайте публичный логин профиля Яндекс Книг. Можно с `@` или без:

```powershell
python main.py scrape <yandex-books-login>
```

или:

```powershell
python main.py scrape @<yandex-books-login>
```

Команда:

- читает публичные цитаты профиля;
- отбрасывает длинные цитаты, русский текст и строки, не похожие на английскую лексику;
- лемматизирует `Front` через spaCy;
- пропускает слова, которые уже есть в `data/cards_enriched.json` с заполненными `Meaning` и `Example`;
- пишет новые кандидаты в `data/quotes_pending.json`.

### 2. Сгенерировать Meaning и Example

```powershell
python main.py enrich
```

Команда читает `data/quotes_pending.json`, обращается к GigaChat и обновляет `data/cards_enriched.json`.

После успешной генерации `quotes_pending.json` переписывается: из него удаляются слова, которые уже стали готовыми карточками.

### 3. Сгенерировать аудио

```powershell
python main.py audio
```

Команда генерирует mp3-файлы в `data/audio` для:

- `Front`;
- `Meaning`;
- `Example`.

Если нужный файл уже существует, он переиспользуется.

### 4. Импортировать в Anki

```powershell
python main.py import
```

Команда:

- создает колоду и note type, если их еще нет;
- загружает аудио в Anki;
- добавляет новые карточки;
- обновляет уже существующие карточки, если поля изменились;
- пропускает полностью совпадающие карточки.

## CSV-импорт

Можно использовать CSV-экспорт цитат вместо или вместе с веб-сбором. На компьютере можно зайти в книгу в Яндекс Книгах и скачать все цитаты в формате CSV.

Это особенно удобно для книг, которые вы уже прочитали: если цитат много, загрузить их через CSV будет намного быстрее, чем собирать все через публичные страницы профиля.

Положите `.csv` файлы в:

```text
data/quotes
```

Затем выполните:

```powershell
python main.py csv
```

Команда создаст или обновит `data/quotes_pending.json` по данным из CSV.

Ожидаемые поля CSV:

- `content` - текст цитаты;
- `book_title` - название книги;
- `book_authors` - авторы.

## Нормализация слов

`Front` приводится к канонической форме:

- текст очищается от лишних пробелов и пунктуации по краям;
- приводится к нижнему регистру;
- лемматизируется через `en_core_web_sm`.

Примеры:

- `grins` -> `grin`;
- `Accolades` -> `accolade`;
- `peered` -> `peer`.

Это нужно, чтобы не создавать несколько карточек для разных форм одного слова.

## Структура проекта

```text
yandex-books-anki/
├── data/
│   ├── quotes/                # CSV-экспорты цитат
│   ├── audio/                 # сгенерированное произношение
│   ├── quotes_pending.json    # новые кандидаты
│   └── cards_enriched.json    # готовые карточки
├── yandex_books_anki/
│   ├── core.py                # общие настройки, нормализация, аудио
│   ├── scraper.py             # сбор цитат из Яндекс Книг и CSV
│   ├── enricher.py            # добавление описания с помощью GigaChat
│   └── anki.py                # отправление карточек в Anki
├── main.py                    # CLI-команды: scrape, csv, enrich, audio, import
├── .env.example               # пример настройки GigaChat
├── requirements.txt           # зависимости проекта
└── README.md                  # описание проекта
```

В Git коммитятся только пустые placeholder-файлы `data/.gitkeep` и `data/quotes/.gitkeep`. Реальные JSON, CSV и mp3-файлы в `data` содержат пользовательские данные и остаются локальными.

