-- Клиентская точка входа (StarterPlayer.StarterPlayerScripts.RojoClient.Main)
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Greeting = require(ReplicatedStorage.RojoShared.Greeting)

print(Greeting.make("клиент"))
