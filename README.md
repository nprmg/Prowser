# Prowser

Код игры Roblox, который синхронизируется в Roblox Studio через [Rojo](https://rojo.space).

## Структура

| Папка | Куда попадает в Studio | Для чего |
|---|---|---|
| `src/server` | `ServerScriptService.Server` | серверные скрипты |
| `src/client` | `StarterPlayer.StarterPlayerScripts.Client` | клиентские скрипты |
| `src/shared` | `ReplicatedStorage.Shared` | общие модули |

Имена файлов задают тип скрипта:

- `*.server.luau` — `Script`
- `*.client.luau` — `LocalScript`
- `*.luau` — `ModuleScript`

Rojo управляет только папками `Server`, `Client` и `Shared`. Всё, что уже есть в игре вне этих папок, он не трогает.

## Первая настройка

1. Установите [Rokit](https://github.com/rojo-rbx/rokit) — менеджер инструментов для Roblox.
2. Склонируйте репозиторий и установите Rojo нужной версии:
   ```sh
   git clone https://github.com/nprmg/Prowser.git
   cd Prowser
   rokit install
   ```
3. Установите плагин Rojo в Studio (версия совпадёт с CLI):
   ```sh
   rojo plugin install
   ```
   Перезапустите Studio, если она была открыта.

## Работа

1. Откройте свою игру в Studio.
2. В папке репозитория запустите сервер Rojo и не закрывайте терминал:
   ```sh
   rojo serve
   ```
3. В Studio: вкладка **Plugins → Rojo → Connect**.
4. Чтобы получить новые изменения из репозитория:
   ```sh
   git pull
   ```
   Rojo сразу перенесёт их в Studio.
5. Сохраните или опубликуйте игру в Studio (`Ctrl+S` / **File → Publish to Roblox**). Rojo меняет открытую сессию, но сам файл игры не сохраняет.

При запуске игры (Play) в Output должны появиться строки
`[Prowser] сервер: ...` и `[Prowser] клиент: ...` — значит, синхронизация работает.
