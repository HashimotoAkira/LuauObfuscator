local function calculateDragonDestination(myPosition, targetPosition)
    local toMeHorizontal = (myPosition - targetPosition) * Vector3.new(1, 0, 1)
    local directionToMe = if toMeHorizontal.Magnitude > 0.1
        then toMeHorizontal.Unit
        else Vector3.new(0, 0, 1)
    local basePosition = targetPosition + directionToMe * 25
    local ceilingPosition = basePosition + Vector3.new(0, 40, 0)
    local travelVector = ceilingPosition - myPosition
    if travelVector.Magnitude > 300 then
        return myPosition + travelVector.Unit * 300
    end
    return ceilingPosition
end

local function buildVelocity(currentVelocity, verticalVelocity)
    return Vector3.new(currentVelocity.X, verticalVelocity, currentVelocity.Z)
end

local function clampTravel(origin, destination, maximumDistance)
    local travel = destination - origin
    if travel.Magnitude > maximumDistance then
        return origin + travel.Unit * maximumDistance
    end
    return destination
end

local worldUp = Vector3.new(0, 1, 0)
local myPosition = Vector3.new(500, 20, 300)
local targetPosition = Vector3.new(0, 0, 0)
local velocity = Vector3.new(12, -4, 8)
local acceleration = Vector3.new(-2, 10, 4)

local horizontal = (myPosition - targetPosition) * Vector3.new(1, 0, 1)
local direction = horizontal.Unit
local angleAxis = direction:Cross(worldUp)
local angle = math.atan2(angleAxis.Magnitude, direction:Dot(worldUp))
local updatedVelocity = velocity + acceleration * 0.25
updatedVelocity *= 0.8
updatedVelocity += Vector3.new(1, -2, 3)
updatedVelocity -= Vector3.new(1, -2, 3)
updatedVelocity /= 2
updatedVelocity *= 2
local normalizedVelocity = updatedVelocity.Unit
local scaledVelocity = normalizedVelocity * updatedVelocity.Magnitude
local reflectedVelocity = -scaledVelocity
local componentScaled = reflectedVelocity * Vector3.new(1, 0, 1)
local scalarScaled = 2 * normalizedVelocity
local halfPosition = myPosition / 2
local rebuiltVelocity = buildVelocity(velocity, math.max(15, velocity.Y))
local positionedVelocity = velocity + Vector3.new(0, -myPosition.Y * 3 - 1, 0)
local velocityArray = {velocity}
local tableVelocity = velocityArray[1] * Vector3.new(1, 0, 1)

local screenDelta = Vector2.new(550, 500) - Vector2.new(400, 300)
local screenDistance = screenDelta.Magnitude
local screenDirection = screenDelta.Unit
local screenNudge = screenDirection * 12
local oppositeScreenDirection = -screenDirection
local screenArray = {screenDelta}
local indexedScreen = screenArray[1] + Vector2.new(2, -3)

local startFrame = CFrame.lookAt(myPosition, targetPosition, worldUp)
local targetFrame = CFrame.lookAt(targetPosition, targetPosition + direction, worldUp)
local smoothedFrame = startFrame:Lerp(targetFrame, 0.35)
local localOffset = CFrame.lookAt(Vector3.zero, worldUp):ToObjectSpace(targetFrame)
local offsetFrame = targetFrame * localOffset:Inverse()
local translatedFrame = CFrame.new(myPosition.X, myPosition.Y, myPosition.Z)
    * CFrame.new(Vector3.new(1, 2, 3))
local lerpedPosition = startFrame:Lerp(targetFrame, 0.35).Position

local destination = calculateDragonDestination(myPosition, targetPosition)
local clampedDestination = clampTravel(myPosition, destination, 300)
local cappedTravel = (clampedDestination - myPosition).Magnitude
local integerDistance = math.floor(screenDistance // 1)
local remainder = integerDistance % 7
local powerAndDivide = (2 ^ 3) / 4

assert(typeof(horizontal) == "Vector3")
assert(typeof(componentScaled) == "Vector3")
assert(typeof(scalarScaled) == "Vector3")
assert(typeof(halfPosition) == "Vector3")
assert(typeof(rebuiltVelocity) == "Vector3")
assert(typeof(positionedVelocity) == "Vector3")
assert(typeof(tableVelocity) == "Vector3")
assert(typeof(screenNudge) == "Vector2")
assert(typeof(oppositeScreenDirection) == "Vector2")
assert(typeof(indexedScreen) == "Vector2")
assert(typeof(smoothedFrame) == "CFrame")
assert(typeof(offsetFrame) == "CFrame")
assert(typeof(translatedFrame) == "CFrame")
assert(typeof(lerpedPosition) == "Vector3")
assert(translatedFrame.Position == myPosition + Vector3.new(1, 2, 3))
assert(math.abs(cappedTravel - 300) < 0.001)
assert(math.abs(screenDistance - 250) < 0.001)
assert(math.abs(screenDirection.X - 0.6) < 0.001)
assert(math.abs(screenDirection.Y - 0.8) < 0.001)
assert(math.abs(tableVelocity.X - velocity.X) < 0.001)
assert(math.abs(tableVelocity.Y) < 0.001)
assert(math.abs(tableVelocity.Z - velocity.Z) < 0.001)
assert(remainder == 5)
assert(powerAndDivide == 2)
assert(math.abs(reflectedVelocity.Magnitude - updatedVelocity.Magnitude) < 0.001)

print(
    "BFP math regression: PASS",
    string.format("%.3f,%.3f,%.3f", destination.X, destination.Y, destination.Z),
    string.format("%.3f", angle),
    string.format("%.3f,%.3f,%.3f", updatedVelocity.X, updatedVelocity.Y, updatedVelocity.Z),
    string.format("%.3f", screenDistance),
    string.format("%.3f,%.3f,%.3f", smoothedFrame.Position.X, smoothedFrame.Position.Y, smoothedFrame.Position.Z),
    string.format("%.3f,%.3f,%.3f", rebuiltVelocity.X, rebuiltVelocity.Y, rebuiltVelocity.Z),
    string.format("%.3f", powerAndDivide)
)
