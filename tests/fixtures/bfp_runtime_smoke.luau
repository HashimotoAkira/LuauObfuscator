local function isNearCenter(position, center)
    local edge = center.X + 0.2
    if position.X >= edge or position.X <= center.X - 0.2 then
        return false
    end
    return position.Y >= center.Y + 0.2 or position.Y < center.Y - 0.2
end

local function makeVectorPair()
    return Vector3.new(3, 4, 0), Vector3.new(0, 0, -1)
end

local function forward(callback, ...)
    return callback(...)
end

local function addVectorComponents(x, y, z)
    return x + y + z
end

local magnitudeVector, lookVector = makeVectorPair()
assert(typeof(magnitudeVector) == "Vector3")
assert(magnitudeVector.Magnitude == 5)
assert((magnitudeVector.Unit - Vector3.new(0.6, 0.8, 0)).Magnitude < 0.00001)
assert(Vector3.zero.Magnitude == 0)

local worldUp = Vector3.new(0, 1, 0)
local axis = Vector3.new(0, 0, 1):Cross(worldUp)
assert(axis == Vector3.new(-1, 0, 0))
assert(worldUp:Dot(worldUp) == 1)
assert((magnitudeVector + Vector3.new(1, 2, 3)) == Vector3.new(4, 6, 3))
assert((magnitudeVector - Vector3.new(1, 2, 3)) == Vector3.new(2, 2, -3))
assert((axis.Unit * 2) == Vector3.new(-2, 0, 0))

local direction = if lookVector.Magnitude >= 0.001
    then lookVector.Unit
    else Vector3.new(0, 0, -1)
local frame = CFrame.lookAt(Vector3.new(10, 0, 20), Vector3.new(10, 0, 20) + direction, worldUp)
assert(typeof(frame) == "CFrame")
assert(frame.Position == Vector3.new(10, 0, 20))
assert(typeof(frame.LookVector) == "Vector3")

local fixed, expandedVector, expandedLook = "fixed", makeVectorPair()
assert(fixed == "fixed")
assert(expandedVector == magnitudeVector and expandedLook == lookVector)

local forwarded = forward(addVectorComponents, 10, table.unpack({20, 12}))
assert(forwarded == 42)

local captured = 40
local callbacks = {}
for _, offset in ipairs({1, 2, 3}) do
    callbacks[offset] = function(value)
        return value + captured + offset
    end
end
assert(callbacks[1](1) == 42)
assert(callbacks[2](1) == 43)
assert(callbacks[3](1) == 44)

local event = Instance.new("BindableEvent")
local received
local connection = event.Event:Connect(function(value)
    received = value
end)
local spawnedCallbackRan = false
task.spawn(function(value)
    spawnedCallbackRan = typeof(value) == "Vector3"
end, magnitudeVector)
task.wait()
assert(spawnedCallbackRan)
task.defer(function()
    event:Fire(magnitudeVector)
end)
local waitedValue = event.Event:Wait()
assert(waitedValue == magnitudeVector)
for _ = 1, 5 do
    if received ~= nil then
        break
    end
    task.wait()
end
assert(received == magnitudeVector, "BindableEvent connection did not observe Fire")
connection:Disconnect()
event:Destroy()

local centered = isNearCenter(Vector2.new(0.1, 0.1), Vector2.zero)
assert(not centered)
assert(math.abs(math.atan2(axis.Magnitude, worldUp:Dot(worldUp))) > 0)
print("BFP VM smoke: PASS", typeof(magnitudeVector), typeof(frame), forwarded)
