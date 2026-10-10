-- Клиентская точка входа (StarterPlayer.StarterPlayerScripts.Client.Main)
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Greeting = require(ReplicatedStorage.Shared.Greeting)

print(Greeting.make("клиент"))
