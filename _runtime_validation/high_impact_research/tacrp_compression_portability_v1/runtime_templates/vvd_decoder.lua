-- Frozen P1 lane decoder plus the separate CVVDPT01 physical-layout branch.
local P1_DOMAIN = "vvd-scalar-tiled-fixup-delta-exact-p1/v1"
local PORTABLE_DOMAIN = "vvd-scalar-tiled-fixup-delta-exact-portable/v1"
local MODE_CONST, MODE_RAW, MODE_DICT = 0, 1, 2
local TILE_SCOPES = { [0] = 0, [1] = 128, [2] = 512, [3] = 2048,
    [4] = 8192, [5] = 32768, [6] = 65536 }
local P1_LAYOUTS = { [1] = "overlay", [2] = "suffix_overlay_tail", [3] = "disjoint" }
local PORTABLE_LAYOUTS = { [1] = "contiguous_no_fixup", [2] = "disjoint_padded" }

local function sha(data)
    local value = util.SHA256(data)
    if type(value) ~= "string" or #value ~= 64 then error("sha256:unavailable") end
    return string.lower(value)
end
local function hex(data)
    local out = {}
    for index = 1, #data do out[index] = string.format("%02x", string.byte(data, index)) end
    return table.concat(out)
end
local function le32(value)
    local a = value % 256; value = math.floor(value / 256)
    local b = value % 256; value = math.floor(value / 256)
    local c = value % 256; value = math.floor(value / 256)
    return string.char(a, b, c, value % 256)
end

local Reader = {}; Reader.__index = Reader
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
    local a, b = string.byte(self:take(2), 1, 2); return a + b * 256
end
function Reader:u32()
    local a, b, c, d = string.byte(self:take(4), 1, 4)
    return a + b * 256 + c * 65536 + d * 16777216
end
function Reader:u64()
    local low, high = self:u32(), self:u32()
    if high > 2097151 then error(self.label .. ":u64_precision") end
    return low + high * 4294967296
end
function Reader:eof() if self.pos ~= #self.data + 1 then error(self.label .. ":trailing") end end

local function bit_width(count)
    local value, width = count - 1, 0
    while value > 0 do width = width + 1; value = math.floor(value / 2) end
    return width
end
local function packed_index(packed, index_zero, width)
    local value, first_bit = 0, index_zero * width
    for bit = 0, width - 1 do
        local absolute = first_bit + bit
        local byte = string.byte(packed, math.floor(absolute / 8) + 1)
        value = value + (math.floor(byte / 2 ^ (absolute % 8)) % 2) * 2 ^ bit
    end
    return value
end
local function word_u32(word)
    local reader = Reader.new(word, "word"); local value = reader:u32(); reader:eof(); return value
end
local function decode_tile(reader, expected_words)
    local mode, index_bits = reader:u8(), reader:u8()
    if reader:u16() ~= 0 then error("tile:reserved") end
    local word_count, dictionary_count, payload_bytes = reader:u32(), reader:u32(), reader:u32()
    if word_count ~= expected_words or word_count < 1 then error("tile:word_count") end
    local payload = reader:take(payload_bytes)
    if mode == MODE_CONST then
        if index_bits ~= 0 or dictionary_count ~= 0 or payload_bytes ~= 4 then error("tile:const") end
        return string.rep(payload, word_count)
    elseif mode == MODE_RAW then
        if index_bits ~= 0 or dictionary_count ~= 0 or payload_bytes ~= word_count * 4 then error("tile:raw") end
        return payload
    elseif mode ~= MODE_DICT or dictionary_count < 2 or index_bits ~= bit_width(dictionary_count) then
        error("tile:dict_shape")
    end
    local index_bytes = math.floor((word_count * index_bits + 7) / 8)
    if payload_bytes ~= dictionary_count * 4 + index_bytes then error("tile:dict_size") end
    local payload_reader, dictionary, previous = Reader.new(payload, "tile_dict"), {}, -1
    for index = 1, dictionary_count do
        local word = payload_reader:take(4); local numeric = word_u32(word)
        if numeric <= previous then error("tile:dictionary_sorted_unique") end
        dictionary[index], previous = word, numeric
    end
    local packed = payload_reader:take(index_bytes); payload_reader:eof()
    local output = {}
    for index = 0, word_count - 1 do
        local id = packed_index(packed, index, index_bits)
        if id >= dictionary_count then error("tile:dict_index") end
        output[index + 1] = dictionary[id + 1]
    end
    local used_bits = word_count * index_bits
    local padding_bits = (8 - used_bits % 8) % 8
    if padding_bits > 0 and math.floor(string.byte(packed, #packed) / 2 ^ (8 - padding_bits)) ~= 0 then
        error("tile:padding_bits")
    end
    return table.concat(output)
end
local function decode_lane(reader, expected_words)
    local scope_code = reader:u8()
    if reader:u8() ~= 0 or reader:u16() ~= 0 then error("lane:reserved") end
    local word_count, tile_count, payload_bytes = reader:u32(), reader:u32(), reader:u32()
    if word_count ~= expected_words or word_count < 1 then error("lane:word_count") end
    local scope = TILE_SCOPES[scope_code]
    if scope == nil then error("lane:scope") end
    local expected_tiles = scope == 0 and 1 or math.floor((word_count + scope - 1) / scope)
    if tile_count ~= expected_tiles then error("lane:tile_count") end
    local payload_reader, output, remaining = Reader.new(reader:take(payload_bytes), "lane_payload"), {}, word_count
    for index = 1, tile_count do
        local words = scope == 0 and remaining or math.min(scope, remaining)
        output[index] = decode_tile(payload_reader, words); remaining = remaining - words
    end
    if remaining ~= 0 then error("lane:remaining") end
    payload_reader:eof()
    local decoded = table.concat(output)
    if #decoded ~= word_count * 4 then error("lane:decoded_size") end
    return decoded
end
local function decode_uleb(reader, maximum)
    local value, multiplier, count = 0, 1, 0
    while true do
        local byte = reader:u8(); count = count + 1; value = value + (byte % 128) * multiplier
        if value > maximum or count > 10 then error("fixup:uleb_overflow") end
        if byte < 128 then
            if count > 1 and byte == 0 then error("fixup:uleb_noncanonical") end
            return value
        end
        multiplier = multiplier * 128
    end
end
local function unzigzag(value) return value % 2 == 0 and value / 2 or -((value + 1) / 2) end
local function decode_fixups(lod_words, delta_payload, row_count, physical_count, active_lods)
    if #lod_words ~= row_count * 4 then error("fixup:lod_lane_size") end
    local reader, parts, previous_end, expanded = Reader.new(delta_payload, "fixups"), {}, 0, {}
    for root = 1, #active_lods do expanded[root] = 0 end
    for index = 1, row_count do
        local source = previous_end + unzigzag(decode_uleb(reader, 8589934591))
        local count = decode_uleb(reader, physical_count)
        if count < 1 or source < 0 or source >= physical_count or count > physical_count - source then
            error("fixup:physical_bounds")
        end
        local lod_word = string.sub(lod_words, (index - 1) * 4 + 1, index * 4)
        local lod = word_u32(lod_word)
        if lod >= #active_lods then error("fixup:lod_range") end
        parts[index] = lod_word .. le32(source) .. le32(count); previous_end = source + count
        for root = 0, #active_lods - 1 do if lod >= root then expanded[root + 1] = expanded[root + 1] + count end end
    end
    reader:eof()
    for root = 1, #active_lods do if expanded[root] ~= active_lods[root] then error("fixup:lod_expansion") end end
    return table.concat(parts)
end
local function interleave(lanes, cells)
    local chunks = {}
    for first = 0, cells - 1, 512 do
        local parts = {}
        for cell = first, math.min(cells - 1, first + 511) do
            for lane = 1, #lanes do
                local offset = cell * 4 + 1; parts[#parts + 1] = string.sub(lanes[lane], offset, offset + 3)
            end
        end
        chunks[#chunks + 1] = table.concat(parts)
    end
    return table.concat(chunks)
end
local function tangent_owned(offset, start_offset, end_offset)
    return start_offset <= offset and offset + 4 <= end_offset
end
local function p1_tangent_words(lane, physical, tangent_start, fixup_start, fixup_end)
    local count = 0
    for cell = 0, physical - 1 do
        if not tangent_owned(tangent_start + cell * 16 + lane * 4, fixup_start, fixup_end) then count = count + 1 end
    end
    return count
end
local function p1_interleave_tangents(lanes, physical, tangent_start, fixup_start, fixup_end)
    local chunks, consumed = {}, { 0, 0, 0, 0 }
    for first = 0, physical - 1, 512 do
        local parts = {}
        for cell = first, math.min(physical - 1, first + 511) do
            for lane = 1, 4 do
                local target = tangent_start + cell * 16 + (lane - 1) * 4
                if not tangent_owned(target, fixup_start, fixup_end) then
                    consumed[lane] = consumed[lane] + 1
                    local offset = (consumed[lane] - 1) * 4 + 1
                    parts[#parts + 1] = string.sub(lanes[lane], offset, offset + 3)
                end
            end
        end
        chunks[#chunks + 1] = table.concat(parts)
    end
    return table.concat(chunks)
end
local function read_vvd_header(header, physical, fixups, lod_count, fixup_start, vertex_start, tangent_start)
    local reader = Reader.new(header, "vvd_header")
    if reader:take(4) ~= "IDSV" or reader:u32() ~= 4 then error("vvd:header") end
    reader:u32()
    if reader:u32() ~= lod_count then error("vvd:lod_count") end
    local lods = {}; for index = 1, 8 do lods[index] = reader:u32() end
    if reader:u32() ~= fixups or reader:u32() ~= fixup_start or reader:u32() ~= vertex_start or
        reader:u32() ~= tangent_start then error("vvd:offset_binding") end
    reader:eof()
    local active = {}
    for index = 1, lod_count do active[index] = lods[index] end
    if active[1] ~= physical then error("vvd:lod0") end
    for index = 2, #active do if active[index - 1] < active[index] then error("vvd:lod_order") end end
    return active
end
local function bind_meta(entry, actual, names)
    local meta = entry.codec_meta
    if type(meta) ~= "table" then error("container:codec_meta") end
    for _, name in ipairs(names) do if meta[name] ~= actual[name] then error("container:contract_binding:" .. name) end end
end

local function decode_p1(blob, entry)
    local reader = Reader.new(blob, "container")
    if reader:take(8) ~= "CVVDP101" or reader:u16() ~= 1 or reader:u16() ~= 192 or reader:u32() ~= 0 then
        error("container:fixed_header")
    end
    local a = { source_size = reader:u64(), physical_count = reader:u32(), fixup_count = reader:u32(), num_lods = reader:u32(),
        vertex_start = reader:u32(), tangent_start = reader:u32(), tangent_end = reader:u32(), fixup_start = reader:u32(),
        fixup_end = reader:u32(), layout_code = reader:u32() }
    local base_count, tangent_count, lane_count, reserved = reader:u16(), reader:u16(), reader:u16(), reader:u16()
    local lod_bytes, delta_bytes = reader:u32(), reader:u32()
    local header, embedded_sha = reader:take(64), hex(reader:take(32))
    if reader:take(20) ~= string.rep("\0", 20) then error("container:reserved") end
    if a.source_size ~= entry.source_size or base_count ~= 12 or tangent_count ~= 4 or lane_count ~= 17 or reserved ~= 0 or
        P1_LAYOUTS[a.layout_code] ~= entry.layout then error("container:geometry") end
    bind_meta(entry, a, { "physical_count", "fixup_count", "num_lods", "vertex_start", "tangent_start",
        "tangent_end", "fixup_start", "fixup_end", "layout_code" })
    local active = read_vvd_header(header, a.physical_count, a.fixup_count, a.num_lods,
        a.fixup_start, a.vertex_start, a.tangent_start)
    local base, tangents = {}, {}
    for lane = 1, 12 do base[lane] = decode_lane(reader, a.physical_count) end
    for lane = 1, 4 do tangents[lane] = decode_lane(reader,
        p1_tangent_words(lane - 1, a.physical_count, a.tangent_start, a.fixup_start, a.fixup_end)) end
    local before = reader.pos; local lod_words = decode_lane(reader, a.fixup_count)
    if reader.pos - before ~= lod_bytes then error("fixup:lod_lane_bytes") end
    local fixup_data = decode_fixups(lod_words, reader:take(delta_bytes), a.fixup_count, a.physical_count, active)
    reader:eof()
    local base_data = interleave(base, a.physical_count)
    local tangent_data = p1_interleave_tangents(tangents, a.physical_count, a.tangent_start, a.fixup_start, a.fixup_end)
    local source
    if entry.layout == "overlay" then source = header .. base_data .. fixup_data .. tangent_data
    elseif entry.layout == "suffix_overlay_tail" then source = header .. base_data .. tangent_data .. fixup_data
    else source = header .. fixup_data .. base_data .. tangent_data end
    if #source ~= a.source_size or sha(source) ~= entry.source_sha256 or sha(source) ~= embedded_sha then
        error("container:source_sha256")
    end
    return source
end

local function decode_portable(blob, entry)
    local reader = Reader.new(blob, "portable")
    if reader:take(8) ~= "CVVDPT01" or reader:u16() ~= 1 or reader:u16() ~= 192 or reader:u32() ~= 0 then
        error("portable:fixed_header")
    end
    local a = { source_size = reader:u64(), physical_count = reader:u32(), fixup_count = reader:u32(), num_lods = reader:u32(),
        vertex_start = reader:u32(), tangent_start = reader:u32(), tangent_end = reader:u32(), fixup_start = reader:u32(),
        fixup_end = reader:u32(), padding_start = reader:u32(), padding_length = reader:u32(), layout_code = reader:u32() }
    local base_count, tangent_count, lod_count, reserved = reader:u16(), reader:u16(), reader:u16(), reader:u16()
    local lod_bytes, delta_bytes = reader:u32(), reader:u32()
    local header, embedded_sha = reader:take(64), hex(reader:take(32))
    if reader:take(12) ~= string.rep("\0", 12) then error("portable:reserved") end
    if a.source_size ~= entry.source_size or base_count ~= 12 or tangent_count ~= 4 or reserved ~= 0 or
        PORTABLE_LAYOUTS[a.layout_code] ~= entry.layout then error("portable:geometry") end
    bind_meta(entry, a, { "physical_count", "fixup_count", "num_lods", "vertex_start", "tangent_start",
        "tangent_end", "fixup_start", "fixup_end", "padding_start", "padding_length", "layout_code" })
    local active = read_vvd_header(header, a.physical_count, a.fixup_count, a.num_lods,
        a.fixup_start, a.vertex_start, a.tangent_start)
    local lanes = {}; for lane = 1, 16 do lanes[lane] = decode_lane(reader, a.physical_count) end
    local fixup_data = ""
    if entry.layout == "contiguous_no_fixup" then
        if a.fixup_count ~= 0 or a.padding_length ~= 0 or lod_count ~= 0 or lod_bytes ~= 0 or delta_bytes ~= 0 then
            error("portable:no_fixup_contract")
        end
    else
        if a.fixup_count < 1 or (a.padding_length ~= 4 and a.padding_length ~= 8 and a.padding_length ~= 12) or
            lod_count ~= 1 then error("portable:padded_contract") end
        local before = reader.pos; local lod_words = decode_lane(reader, a.fixup_count)
        if reader.pos - before ~= lod_bytes then error("portable:lod_lane_bytes") end
        fixup_data = decode_fixups(lod_words, reader:take(delta_bytes), a.fixup_count, a.physical_count, active)
    end
    reader:eof()
    local base = {}; for index = 1, 12 do base[index] = lanes[index] end
    local tangents = {}; for index = 1, 4 do tangents[index] = lanes[index + 12] end
    local base_data, tangent_data = interleave(base, a.physical_count), interleave(tangents, a.physical_count)
    local source = header .. (entry.layout == "disjoint_padded" and
        (fixup_data .. string.rep("\0", a.padding_length)) or "") .. base_data .. tangent_data
    if #source ~= a.source_size or sha(source) ~= entry.source_sha256 or sha(source) ~= embedded_sha then
        error("portable:source_sha256")
    end
    return source
end

local function decode_entry(blob, entry)
    if entry.codec_domain == P1_DOMAIN then return decode_p1(blob, entry) end
    if entry.codec_domain == PORTABLE_DOMAIN then return decode_portable(blob, entry) end
    error("container:codec_domain")
end
return decode_entry
