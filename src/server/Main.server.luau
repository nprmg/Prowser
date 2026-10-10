-- Серверная точка входа (ServerScriptService.RojoServer.Main)
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Greeting = require(ReplicatedStorage.RojoShared.Greeting)

print(Greeting.make("сервер"))
