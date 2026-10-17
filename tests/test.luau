--!strict
-- Luau smoke test: run this file to see each feature's output.

type Vector2Like = { x: number, y: number }

local function clamp(x: number, lo: number, hi: number): number
	if x < lo then
		return lo
	elseif x > hi then
		return hi
	end
	return x
end

print("=== annotations and if/elseif/else ===")
print("clamp:", clamp(5, 1, 4), clamp(-2, 1, 4), clamp(3, 1, 4))

local function onHeartbeat(dt: number): (boolean, number, string, string)
	local count: number = 0
	for i = 1, 10 do
		if i == 5 then
			print("continue at:", i)
			continue
		end
		count += i
		if i > 8 then
			print("break at:", i)
			break
		end
	end
	print("numeric for total:", count)

	local values = { 1, 2, 3, name = "hero", ["key" .. 1] = true }
	local sum = 0
	for key, value in pairs(values) do
		if type(value) == "number" then
			sum += value
			print("numeric table item:", key, value)
		end
	end
	print("generic for total:", sum)

	local ok = (count > 0) and (sum >= 0) or false
	local casted = count :: any
	local short = "short string: -- is not a comment"
	local long = [[long
string]]
	print("boolean and cast:", ok, casted)
	print(short)
	print(long)
	return ok, casted, short, long
end

print("=== function return values ===")
local ok, casted, short, long = onHeartbeat(1 / 60)
print("returned:", ok, casted, short, long)

print("=== table and method calls ===")
local obj = {}
function obj.new(x, y)
	local self = setmetatable({}, { __index = obj })
	self.x, self.y = x, y
	return self
end

function obj:getSum()
	return self.x + self.y
end

local point = obj.new(3, 4)
print("method result:", point:getSum())

print("=== repeat/until ===")
local attempts = 0
repeat
	attempts += 1
	print("attempt:", attempts)
until attempts >= 3

print("=== cast and typed local ===")
local pointLike: Vector2Like = { x = 6, y = 8 }
local untyped = pointLike :: any
print("point:", untyped.x, untyped.y)
print("=== done ===")
