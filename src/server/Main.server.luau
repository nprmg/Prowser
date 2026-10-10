-- Серверная точка входа (ServerScriptService.Server.Main)
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Greeting = require(ReplicatedStorage.Shared.Greeting)

print(Greeting.make("сервер"))
