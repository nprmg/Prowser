-- Общий модуль: доступен и серверу, и клиенту (ReplicatedStorage.Shared.Greeting)
local Greeting = {}

function Greeting.make(side: string): string
	return `[Prowser] {side}: Rojo синхронизирует код из репозитория`
end

return Greeting
