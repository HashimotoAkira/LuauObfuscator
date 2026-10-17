--!strict
-- test sample cho parser
local part: Part = workspace.Part
local _floor = math.floor

type Vector2Like = { x: number, y: number }

local function clamp(x: number, lo: number, hi: number): number
	if x < lo then
		return lo
	elseif x > hi then
		return hi
	end
	return x
end

local function onHeartbeat(dt)
	local count = 0
	for i = 1, 10 do
		count += i
		if i == 5 then
			continue
		end
		if i > 8 then
			break
		end
	end

	local t = { 1, 2, 3, name = "hero", [ "key" .. 1 ] = true }
	local sum = 0
	for k, v in pairs(t) do
		if type(v) == "number" then
			sum = sum + v
		end
	end

	local ok = (count > 0) and (sum >= 0) or false
	local casted = count :: any
	local s = "line1\nline2\ttab \65\x41\u{1F600}"
	local long = [[
multi
line]]

	part.Position = part.Position + Vector3.new(0, dt, 0)
	part:Destroy()

	return ok, casted, s, long
end

local obj = {}
function obj.new(x, y)
	local self = setmetatable({}, obj)
	self.x, self.y = x, y
	return self
end

function obj:getSum()
	return self.x + self.y
end

repeat
	onHeartbeat(1/60)
until false

local a, b, c = 1, 2;
::continue_point::
goto continue_point
