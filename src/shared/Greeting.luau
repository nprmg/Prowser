-- Общий модуль: доступен и серверу, и клиенту (ReplicatedStorage.RojoShared.Greeting)
local Greeting = {}

function Greeting.make(side: string): string
	return `[Prowser] {side}: Rojo синхронизирует код из репозитория`
end

return Greeting
