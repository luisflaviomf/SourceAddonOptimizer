-- Extracted from the frozen exact P2 runtime. This chunk executes inside a closure.
local function sha(data)
    local value = util.SHA256(data)
    if type(value) ~= "string" or #value ~= 64 then error("sha256:unavailable") end
    return string.lower(value)
end

local function integer(value, minimum, maximum, label)
    if type(value) ~= "string" or value == "" or not string.match(value, "^[0-9]+$") or
        (#value > 1 and string.sub(value, 1, 1) == "0") then error(label .. ":decimal") end
    local parsed = tonumber(value)
    if parsed == nil or parsed ~= math.floor(parsed) or parsed < minimum or parsed > maximum then
        error(label .. ":bounds")
    end
    return parsed
end

local function signed_integer(value, minimum, maximum, label)
    if value == "-1" and minimum <= -1 then return -1 end
    return integer(value, math.max(0, minimum), maximum, label)
end

local Reader = {}
Reader.__index = Reader
function Reader.new(data, label) return setmetatable({ data = data, pos = 1, label = label }, Reader) end
function Reader:take(count)
    if count < 0 or count ~= math.floor(count) or self.pos + count - 1 > #self.data then
        error(self.label .. ":truncated")
    end
    local value = string.sub(self.data, self.pos, self.pos + count - 1)
    self.pos = self.pos + count
    return value
end
function Reader:u8() return string.byte(self:take(1), 1) end
function Reader:u16()
    local a, b = string.byte(self:take(2), 1, 2)
    return a + b * 256
end
function Reader:u32()
    local a, b, c, d = string.byte(self:take(4), 1, 4)
    return a + b * 256 + c * 65536 + d * 16777216
end

local function byte_hex(data)
    local output = {}
    for index = 1, #data do output[index] = string.format("%02x", string.byte(data, index)) end
    return table.concat(output)
end

local function bit_width(count)
    local value, width = count - 1, 0
    while value > 0 do width = width + 1; value = math.floor(value / 2) end
    return width
end

local function packed_length(count, width)
    local bits = count * width
    if bits > 536870912 then error("container:packed_overflow") end
    return math.floor((bits + 7) / 8)
end
local function read_id(data, packed_offset, index_zero, width)
    local value, bit_offset = 0, index_zero * width
    for bit = 0, width - 1 do
        local absolute = bit_offset + bit
        local byte = string.byte(data, packed_offset + math.floor(absolute / 8))
        value = value + (math.floor(byte / (2 ^ (absolute % 8))) % 2) * (2 ^ bit)
    end
    return value
end

local function parse_sequence(reader, value_width, value_count)
    local mode = reader:u8()
    local raw_cost = 1 + value_count * value_width
    if mode == 0 then
        local values_offset = reader.pos
        local values, unique = reader:take(value_count * value_width), {}
        local unique_count = 0
        for offset = 1, #values, value_width do
            local value = string.sub(values, offset, offset + value_width - 1)
            if unique[value] == nil then unique[value] = true; unique_count = unique_count + 1 end
        end
        local dictionary_cost = 5 + unique_count * value_width + packed_length(value_count, bit_width(unique_count))
        if dictionary_cost < raw_cost then error("sequence:nonminimal_raw") end
        return { mode = 0, values_offset = values_offset, width = value_width }
    end
    if mode ~= 1 then error("sequence:mode") end
    local dictionary_count = reader:u32()
    if dictionary_count < 1 or dictionary_count > value_count then error("sequence:dictionary_count") end
    local dictionary_offset = reader.pos
    local seen = {}
    for index = 1, dictionary_count do
        local value = reader:take(value_width)
        if seen[value] then error("sequence:dictionary_duplicate") end
        seen[value] = true
    end
    local index_bits = bit_width(dictionary_count)
    local index_bytes = packed_length(value_count, index_bits)
    local packed_offset = reader.pos
    local packed = reader:take(index_bytes)
    if 5 + dictionary_count * value_width + index_bytes >= raw_cost then error("sequence:nonminimal_dictionary") end
    local used_bits = value_count * index_bits
    local padding = (8 - used_bits % 8) % 8
    if padding > 0 then
        local final = string.byte(packed, #packed)
        if math.floor(final / (2 ^ (8 - padding))) ~= 0 then error("sequence:padding") end
    end
    local output, next_first_seen = {}, 0
    for index = 0, value_count - 1 do
        local id = read_id(reader.data, packed_offset, index, index_bits)
        if id >= dictionary_count then error("sequence:index") end
        if id == next_first_seen then next_first_seen = next_first_seen + 1
        elseif id > next_first_seen then error("sequence:first_seen_order") end
    end
    if next_first_seen ~= dictionary_count then error("sequence:unused_dictionary") end
    return { mode = 1, dictionary_offset = dictionary_offset, packed_offset = packed_offset,
        bit_width = index_bits, width = value_width }
end

local function sequence_value(blob, sequence, index_zero)
    if sequence.mode == 0 then
        local offset = sequence.values_offset + index_zero * sequence.width
        return string.sub(blob, offset, offset + sequence.width - 1)
    end
    local id = read_id(blob, sequence.packed_offset, index_zero, sequence.bit_width)
    local offset = sequence.dictionary_offset + id * sequence.width
    return string.sub(blob, offset, offset + sequence.width - 1)
end

local function next_delimited(state, delimiter, label)
    local found = string.find(state.data, delimiter, state.pos, true)
    if found == nil then
        local value = string.sub(state.data, state.pos)
        if value == "" then error(label) end
        state.pos = #state.data + 1
        return value, true
    end
    local value = string.sub(state.data, state.pos, found - 1)
    if value == "" then error(label) end
    state.pos = found + #delimiter
    return value, false
end

local function named_part(state, name, last)
    local part, actual_last = next_delimited(state, ",", "layout:part")
    if actual_last ~= last then error("layout:shape") end
    local prefix = name .. "="
    if string.sub(part, 1, #prefix) ~= prefix then error("layout:name") end
    return string.sub(part, #prefix + 1)
end

local function parse_layout(value, source_size)
    local state, layout = { data = value, pos = 1 }, {}
    layout.major = integer(named_part(state, "major", false), 7, 7, "layout:major")
    layout.minor = integer(named_part(state, "minor", false), 1, 4, "layout:minor")
    if layout.minor ~= 1 and layout.minor ~= 2 and layout.minor ~= 4 then error("layout:minor") end
    layout.header = integer(named_part(state, "header", false), 64, 96, "layout:header")
    layout.width = integer(named_part(state, "width", false), 1, 16384, "layout:width")
    layout.height = integer(named_part(state, "height", false), 1, 16384, "layout:height")
    layout.flags = integer(named_part(state, "flags", false), 0, 4294967295, "layout:flags")
    layout.frames = integer(named_part(state, "frames", false), 1, 65535, "layout:frames")
    layout.first = integer(named_part(state, "first", false), 0, 65535, "layout:first")
    layout.depth = integer(named_part(state, "depth", false), 1, 16384, "layout:depth")
    layout.high_format = integer(named_part(state, "high-format", false), 13, 15, "layout:high_format")
    if layout.high_format ~= 13 and layout.high_format ~= 15 then error("layout:high_format") end
    layout.low_format = signed_integer(named_part(state, "low-format", false), -1, 15, "layout:low_format")
    if layout.low_format ~= -1 and layout.low_format ~= 13 and layout.low_format ~= 15 then error("layout:low_format") end
    layout.low_width = integer(named_part(state, "low-width", false), 0, 255, "layout:low_width")
    layout.low_height = integer(named_part(state, "low-height", false), 0, 255, "layout:low_height")
    layout.mips = integer(named_part(state, "mips", false), 1, 16, "layout:mips")
    layout.faces = integer(named_part(state, "faces", false), 1, 7, "layout:faces")
    if layout.faces ~= 1 and layout.faces ~= 6 and layout.faces ~= 7 then error("layout:faces") end
    layout.low_offset = integer(named_part(state, "low-offset", false), 1, 67108864, "layout:low_offset")
    layout.low_length = integer(named_part(state, "low-length", false), 0, 67108864, "layout:low_length")
    layout.high_offset = integer(named_part(state, "high-offset", false), 1, 67108864, "layout:high_offset")
    layout.high_length = integer(named_part(state, "high-length", false), 1, 67108864, "layout:high_length")
    layout.trailing = integer(named_part(state, "trailing", false), 0, 67108864, "layout:trailing")
    layout.mip_bytes = {}
    local mip_state = { data = named_part(state, "mip-bytes", false), pos = 1 }
    for index = 1, layout.mips do
        local item, last = next_delimited(mip_state, ":", "layout:mip_bytes")
        if last ~= (index == layout.mips) then error("layout:mip_count") end
        layout.mip_bytes[index] = integer(item, 1, 67108864, "layout:mip_bytes")
    end
    layout.resources = {}
    local resource_text = named_part(state, "resources", true)
    if resource_text ~= "-" then
        local resource_state = { data = resource_text, pos = 1 }
        for index = 1, 2 do
            local item, last = next_delimited(resource_state, ";", "layout:resources")
            local fields, resource = { data = item, pos = 1 }, {}
            local tag, a = next_delimited(fields, ":", "layout:resource")
            local flags, b = next_delimited(fields, ":", "layout:resource")
            local data, c = next_delimited(fields, ":", "layout:resource")
            local inline, d = next_delimited(fields, ":", "layout:resource")
            if a or b or c or not d then error("layout:resource_shape") end
            resource.tag = integer(tag, 0, 16777215, "layout:resource_tag")
            resource.flags = integer(flags, 0, 255, "layout:resource_flags")
            resource.data = integer(data, 0, 4294967295, "layout:resource_data")
            resource.inline = integer(inline, 0, 1, "layout:resource_inline")
            layout.resources[index] = resource
            if last ~= (index == 2) then error("layout:resource_count") end
        end
    end
    if state.pos ~= #state.data + 1 or layout.high_offset + layout.high_length + layout.trailing ~= source_size or
        layout.low_offset + layout.low_length ~= layout.high_offset then error("layout:source_bounds") end
    return layout
end

local function parse_codec(entry)
    local state = { data = entry.codec_parameters, pos = 1 }
    entry.strategy = integer(named_part(state, "strategy", false), 1, 3, "codec:strategy")
    entry.tile = integer(named_part(state, "tile", false), 0, 65536, "codec:tile")
    entry.rank = integer(named_part(state, "rank", true), 1, 11, "codec:rank")
    local tiles = { 128, 256, 512, 1024, 2048, 4096, 8192, 16384, 65536 }
    local expected = entry.strategy == 1 and entry.tile == 0 and 1 or
        (entry.strategy == 2 and entry.tile == 0 and 2 or nil)
    if entry.strategy == 3 then
        for index, tile in ipairs(tiles) do if entry.tile == tile then expected = index + 2 end end
    end
    if expected ~= entry.rank then error("codec:tuple") end
end

local function replay_vtf(source, entry)
    local layout, reader = entry._layout, Reader.new(source, "vtf")
    if #source ~= entry.source_size or reader:take(4) ~= "VTF\0" or reader:u32() ~= 7 then error("vtf:identity") end
    local minor, header, width, height = reader:u32(), reader:u32(), reader:u16(), reader:u16()
    local flags, frames, first = reader:u32(), reader:u16(), reader:u16()
    reader:take(24)
    local high_format, mips, low_format = reader:u32(), reader:u8(), reader:u32()
    if low_format >= 2147483648 then low_format = low_format - 4294967296 end
    local low_width, low_height = reader:u8(), reader:u8()
    local depth = minor >= 2 and reader:u16() or 1
    if minor ~= layout.minor or header ~= layout.header or width ~= layout.width or height ~= layout.height or
        flags ~= layout.flags or frames ~= layout.frames or first ~= layout.first or high_format ~= layout.high_format or
        mips ~= layout.mips or low_format ~= layout.low_format or low_width ~= layout.low_width or
        low_height ~= layout.low_height or depth ~= layout.depth then error("vtf:layout_identity") end
    if first ~= 65535 and first >= frames then error("vtf:first_frame") end
    local block_size = high_format == 13 and 8 or 16
    local low_length = 0
    if low_format == -1 then
        if low_width ~= 0 or low_height ~= 0 then error("vtf:thumbnail_absent") end
    else
        if (low_format ~= 13 and low_format ~= 15) or low_width < 1 or low_height < 1 then error("vtf:thumbnail") end
        local low_block = low_format == 13 and 8 or 16
        low_length = math.max(1, math.floor((low_width + 3) / 4)) *
            math.max(1, math.floor((low_height + 3) / 4)) * low_block
    end
    local low_offset, high_offset = header, header + low_length
    if minor == 4 then
        if header ~= 96 or #layout.resources ~= 2 then error("vtf:resource_header") end
        local resource_count = string.byte(source, 69) + string.byte(source, 70) * 256 +
            string.byte(source, 71) * 65536 + string.byte(source, 72) * 16777216
        if resource_count ~= 2 then error("vtf:resource_count") end
        local saw_low, saw_high, saw_crc = false, false, false
        for index = 1, 2 do
            local offset, bound = 81 + (index - 1) * 8, layout.resources[index]
            local tag = string.byte(source, offset) + string.byte(source, offset + 1) * 256 + string.byte(source, offset + 2) * 65536
            local resource_flags = string.byte(source, offset + 3)
            local data = string.byte(source, offset + 4) + string.byte(source, offset + 5) * 256 +
                string.byte(source, offset + 6) * 65536 + string.byte(source, offset + 7) * 16777216
            if tag ~= bound.tag or resource_flags ~= bound.flags or data ~= bound.data or
                ((resource_flags == 2) and 1 or 0) ~= bound.inline then error("vtf:resource_identity") end
            if tag == 1 and resource_flags == 0 then saw_low = true; low_offset = data end
            if tag == 48 and resource_flags == 0 then saw_high = true; high_offset = data end
            if tag == 4411971 and resource_flags == 2 then saw_crc = true end
        end
        if not saw_high or (low_format ~= -1 and not saw_low) or
            (low_format == -1 and (saw_low or not saw_crc)) then error("vtf:resource_set") end
    elseif #layout.resources ~= 0 then error("vtf:legacy_resources") end
    local actual_faces = math.floor(flags / 16384) % 2 == 0 and 1 or
        ((minor >= 5 or first == 65535) and 6 or 7)
    if actual_faces ~= layout.faces then error("vtf:faces") end
    local high_length = 0
    for level = 0, mips - 1 do
        local level_length = math.max(1, math.floor((math.max(1, math.floor(width / 2 ^ level)) + 3) / 4)) *
            math.max(1, math.floor((math.max(1, math.floor(height / 2 ^ level)) + 3) / 4)) * block_size *
            math.max(1, math.floor(depth / 2 ^ level)) * frames * layout.faces
        if level_length ~= layout.mip_bytes[level + 1] then error("vtf:mip_length") end
        high_length = high_length + level_length
    end
    if low_offset ~= layout.low_offset or low_length ~= layout.low_length or high_offset ~= layout.high_offset or
        high_length ~= layout.high_length or high_offset + high_length ~= #source or layout.trailing ~= 0 then
        error("vtf:range_identity")
    end
end

local function decode_entry(blob, entry)
    if type(blob) ~= "string" or #blob < 96 or #blob > 67108864 then error("container:length") end
    entry._layout = entry._layout or parse_layout(entry.layout, entry.source_size)
    if entry.strategy == nil then parse_codec(entry) end
    local layout, reader = entry._layout, Reader.new(blob, "container")
    if reader:take(8) ~= "CVTFP002" or reader:u16() ~= 1 or reader:u16() ~= 96 then error("container:identity") end
    local strategy, format, tile_code, field_count = reader:u8(), reader:u8(), reader:u8(), reader:u8()
    local major, minor, mips, faces = reader:u8(), reader:u8(), reader:u8(), reader:u8()
    local width, height, frames, depth = reader:u16(), reader:u16(), reader:u16(), reader:u16()
    local high_offset, high_length, suffix_length, source_length = reader:u32(), reader:u32(), reader:u32(), reader:u32()
    local block_count, segment_count, source_sha = reader:u32(), reader:u32(), byte_hex(reader:take(32))
    local source_header, resource_count, low_offset, low_length = reader:u16(), reader:u16(), reader:u32(), reader:u32()
    if reader.pos ~= 97 or strategy ~= entry.strategy or format ~= layout.high_format or major ~= layout.major or
        minor ~= layout.minor or mips ~= layout.mips or faces ~= layout.faces or width ~= layout.width or
        height ~= layout.height or frames ~= layout.frames or depth ~= layout.depth or high_offset ~= layout.high_offset or
        high_length ~= layout.high_length or suffix_length ~= layout.trailing or source_length ~= entry.source_size or
        source_sha ~= entry.source_sha256 or source_header ~= layout.header or resource_count ~= #layout.resources or
        low_offset ~= layout.low_offset or low_length ~= layout.low_length then error("container:entry_binding") end
    local tiles = { 128, 256, 512, 1024, 2048, 4096, 8192, 16384, 65536 }
    local tile = tile_code == 0 and 0 or tiles[tile_code]
    local block_size = format == 13 and 8 or (format == 15 and 16 or 0)
    local widths = format == 13 and { 4, 4 } or { 2, 6, 4, 4 }
    if strategy == 1 then widths = { block_size } end
    if block_size == 0 or tile ~= entry.tile or high_length % block_size ~= 0 or block_count ~= high_length / block_size then
        error("container:block_layout")
    end
    if (strategy == 1 and (tile_code ~= 0 or field_count ~= 1)) or
        (strategy == 2 and (tile_code ~= 0 or field_count ~= #widths)) or
        (strategy == 3 and (tile == nil or tile == 0 or field_count ~= #widths)) then
        error("container:strategy_tuple")
    end
    local expected_segments = strategy == 3 and math.floor((block_count + tile - 1) / tile) or 1
    if segment_count ~= expected_segments or high_offset + high_length + suffix_length ~= source_length then
        error("container:segment_or_source_bounds")
    end
    local chunks, consumed = { reader:take(high_offset) }, 0
    for _ = 1, segment_count do
        local count = strategy == 3 and math.min(tile, block_count - consumed) or block_count
        local sequences = {}
        for field = 1, #widths do sequences[field] = parse_sequence(reader, widths[field], count) end
        local batch, batch_blocks = "", 0
        for value = 0, count - 1 do
            local block = ""
            for field = 1, #widths do block = block .. sequence_value(blob, sequences[field], value) end
            if #block ~= block_size then error("container:block_width") end
            batch = batch .. block
            batch_blocks = batch_blocks + 1
            if batch_blocks == 256 then chunks[#chunks + 1] = batch; batch = ""; batch_blocks = 0 end
        end
        if #batch > 0 then chunks[#chunks + 1] = batch end
        consumed = consumed + count
    end
    chunks[#chunks + 1] = reader:take(suffix_length)
    if reader.pos ~= #blob + 1 or consumed ~= block_count then error("container:eof") end
    local source = table.concat(chunks)
    if #source ~= source_length or sha(source) ~= source_sha then error("container:source_sha") end
    replay_vtf(source, entry)
    return source
end

return decode_entry
