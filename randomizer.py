from time import time
from sys import argv
from shutil import copyfile
from hashlib import md5
from utils import (hex2int, int2bytes, ENEMY_TABLE, ESPER_TABLE, CHEST_TABLE,
                   CHAR_TABLE, COMMAND_TABLE, read_multi, write_multi,
                   texttable, utilrandom as random)
from skillrandomizer import SpellBlock, CommandBlock, get_ranked_spells
from monsterrandomizer import (MonsterBlock, MonsterGraphicBlock,
                               MetamorphBlock, get_ranked_monsters,
                               shuffle_monsters)
from itemrandomizer import (ItemBlock, reset_equippable, get_ranked_items,
                            reset_special_relics, reset_rage_blizzard)
from chestrandomizer import ChestBlock, shuffle_locations, shuffle_monster_boxes
from esperrandomizer import EsperBlock
from shoprandomizer import ShopBlock
from namerandomizer import generate_name
from formationrandomizer import Formation, FormationSet


VERSION = "6"
VERBOSE = False


NEVER_REPLACE = ["fight", "item", "magic", "row", "def", "magitek", "lore",
                 "jump", "mimic", "xmagic", "summon", "morph", "revert"]
ALWAYS_REPLACE = ["leap", "possess", "health", "shock"]


MD5HASH = "e986575b98300f721ce27c180264d890"

# Dummied Umaro, Dummied Kefka, Colossus, CzarDragon, ???, ???
REPLACE_ENEMIES = [0x10f, 0x11a, 0x136, 0x137]
# fake Atma, Guardian x4
REPLACE_FORMATIONS = [0x1ff, 0x20e]


class Substitution(object):
    location = None

    @property
    def size(self):
        return len(self.bytestring)

    def set_location(self, location):
        self.location = location

    def write(self, filename):
        f = open(filename, 'r+b')
        bs = "".join(map(chr, self.bytestring))
        f.seek(self.location)
        f.write(bs)
        f.close()


class AutoLearnRageSub(Substitution):
    def __init__(self, require_gau):
        self.require_gau = require_gau

    @property
    def bytestring(self):
        # NOTE: This must be placed at a location called from C2/5EE5
        bs = []
        if self.require_gau:
            bs += [0xAD, 0x0B, 0x30, 0x30, 0x03]
        bs += [0x20, 0x07, 0x4A, 0xAD, 0x0A, 0x30, 0x60]
        return bs

    def write(self, filename):
        learn_leap_sub = Substitution()
        learn_leap_sub.bytestring = [0xEA] * 7
        learn_leap_sub.set_location(0x2543E)
        learn_leap_sub.write(filename)

        vict_sub = Substitution()
        vict_sub.bytestring = [0x20] + int2bytes(self.location, length=2)
        vict_sub.set_location(0x25EE5)
        vict_sub.write(filename)

        super(AutoLearnRageSub, self).write(filename)


class AutoRecruitGauSub(Substitution):
    @property
    def bytestring(self):
        return [0x50, 0xBC, 0x59, 0x10, 0x3F, 0x0B, 0x01, 0xD4, 0xFB, 0xFE]

    def write(self, filename):
        sub_addr = self.location - 0xa0000
        call_recruit_sub = Substitution()
        call_recruit_sub.bytestring = [0xB2] + int2bytes(sub_addr, length=3)
        call_recruit_sub.set_location(0xBC19C)
        call_recruit_sub.write(filename)
        gau_stays_wor_sub = Substitution()
        gau_stays_wor_sub.bytestring = [0xD4, 0xFB]
        gau_stays_wor_sub.set_location(0xA5324)
        gau_stays_wor_sub.write(filename)
        gau_cant_appear_sub = Substitution()
        gau_cant_appear_sub.bytestring = [0x80, 0x0C]
        gau_cant_appear_sub.set_location(0x22FB5)
        gau_cant_appear_sub.write(filename)
        REPLACE_ENEMIES.append(0x172)
        super(AutoRecruitGauSub, self).write(filename)


class SpellSub(Substitution):
    def __init__(self, spellid):
        self.spellid = spellid
        self.bytestring = [0xA9, self.spellid, 0x85, 0xB6, 0xA9,
                           0x02, 0x85, 0xB5, 0x4C, 0x5F, 0x17]


class EnableEsperMagicSub(Substitution):
    @property
    def bytestring(self):
        return [0xA9, 0x20, 0xA6, 0x00, 0x95, 0x79, 0xE8, 0xA9, 0x24, 0x60]

    def write(self, filename):
        jsr_sub = Substitution()
        jsr_sub.bytestring = [0x20] + int2bytes(self.location, length=2) + [0xEA]
        jsr_sub.set_location(0x34D3D)
        jsr_sub.write(filename)
        super(EnableEsperMagicSub, self).write(filename)


class FreeBlock:
    def __init__(self, start, end):
        self.start = start
        self.end = end

    @property
    def size(self):
        return self.end - self.start

    def unfree(self, start, length):
        end = start + length
        if start < self.start:
            raise Exception("Used space out of bounds (left)")
        elif end > self.end:
            raise Exception("Used space out of bounds (right)")
        newfree = []
        if self.start != start:
            newfree.append(FreeBlock(self.start, start))
        if end != self.end:
            newfree.append(FreeBlock(end, self.end))
        self.start, self.end = None, None
        return newfree


equip_offsets = {"weapon": 15,
                 "shield": 16,
                 "helm": 17,
                 "armor": 18,
                 "relic1": 19,
                 "relic2": 20}


class CharacterBlock:
    def __init__(self, address, name):
        self.address = hex2int(address)
        self.name = name.lower()
        self.battle_commands = [None, None, None, None]
        self.id = None
        self.beserk = False

    def set_battle_command(self, slot, command=None, command_id=None):
        if command:
            command_id = command.id
        self.battle_commands[slot] = command_id

    def write_battle_commands(self, filename):
        f = open(filename, 'r+b')
        for i, command in enumerate(self.battle_commands):
            if command is None:
                continue
            f.seek(self.address + 2 + i)
            f.write(chr(command))
        f.close()

    def write_default_equipment(self, filename, equipid, equiptype):
        f = open(filename, 'r+b')
        f.seek(self.address + equip_offsets[equiptype])
        f.write(chr(equipid))
        f.close()

    def mutate_stats(self, filename):
        f = open(filename, 'r+b')

        def mutation(base):
            while True:
                value = max(base / 2, 1)
                if self.beserk:
                    value += 1

                value += random.randint(0, value) + random.randint(0, value)
                while random.randint(1, 10) == 10:
                    value = max(value / 2, 1)
                    value += random.randint(0, value) + random.randint(0, value)
                value = max(1, min(value, 0xFE))

                if not self.beserk:
                    break
                elif value >= base:
                    break

            return value

        f.seek(self.address)
        hpmp = map(ord, f.read(2))
        hpmp = map(lambda v: mutation(v), hpmp)
        f.seek(self.address)
        f.write("".join(map(chr, hpmp)))

        f.seek(self.address + 6)
        stats = map(ord, f.read(9))
        stats = map(lambda v: mutation(v), stats)
        f.seek(self.address + 6)
        f.write("".join(map(chr, stats)))

        f.close()

    def set_id(self, i):
        self.id = i
        if self.id == 13:
            self.beserk = True


def commands_from_table(tablefile):
    commands = []
    for i, line in enumerate(open(tablefile)):
        line = line.strip()
        if line[0] == '#':
            continue

        while '  ' in line:
            line = line.replace('  ', ' ')
        c = CommandBlock(*line.split(','))
        c.set_id(i)
        commands.append(c)
    return commands


monsterdict = {}


def monsters_from_table(tablefile):
    monsters = []
    for i, line in enumerate(open(tablefile)):
        line = line.strip()
        if line[0] == '#':
            continue

        while '  ' in line:
            line = line.replace('  ', ' ')
        c = MonsterBlock(*line.split(','))
        c.set_id(i)
        monsterdict[i] = c
        monsters.append(c)
    return monsters


def get_formations(filename):
    baseptr = 0xf6200
    formations = []
    for i in xrange(576):
        f = Formation(i)
        f.read_data(filename)
        f.lookup_enemies(monsterdict)
        f.read_mould(filename)
        formations.append(f)
    return formations


def characters_from_table(tablefile):
    characters = []
    for i, line in enumerate(open(tablefile)):
        line = line.strip()
        if line[0] == '#':
            continue

        while '  ' in line:
            line = line.replace('  ', ' ')
        c = CharacterBlock(*line.split(','))
        c.set_id(i)
        characters.append(c)
    return characters


def items_from_table(tablefile):
    items = []
    for i, line in enumerate(open(tablefile)):
        line = line.strip()
        if line[0] == '#':
            continue

        while '  ' in line:
            line = line.replace('  ', ' ')
        c = ItemBlock(*line.split(','))
        items.append(c)
    return items


def chests_from_table(tablefile):
    items = []
    for i, line in enumerate(open(tablefile)):
        line = line.strip()
        if line[0] == '#':
            continue

        while '  ' in line:
            line = line.replace('  ', ' ')
        c = ChestBlock(*line.split(','))
        items.append(c)
    return items


def espers_from_table(tablefile):
    espers = []
    for i, line in enumerate(open(tablefile)):
        line = line.strip()
        if line[0] == '#':
            continue

        while '  ' in line:
            line = line.replace('  ', ' ')
        c = EsperBlock(*line.split(','))
        espers.append(c)
    return espers


def randomize_colosseum(filename, pointer):
    item_objs = get_ranked_items(filename)
    monster_objs = get_ranked_monsters(filename, bosses=False)
    items = [i.itemid for i in item_objs]
    monsters = [m.id for m in monster_objs]
    f = open(filename, 'r+b')
    for i in range(0xFF):
        #if i == 0x29:
        #    continue  # striker

        index = items.index(i)
        trade = index
        while index == trade:
            trade = index
            while random.randint(1, 3) < 3:
                trade += random.randint(-3, 3)
                trade = max(0, min(trade, len(items)-1))

        opponent = trade
        opponent = max(0, min(opponent, len(monsters)-1))
        while random.randint(1, 3) < 3:
            opponent += random.randint(-1, 1)
            opponent = max(0, min(opponent, len(monsters)-1))
        trade = items[trade]
        opponent = monsters[opponent]
        wager_obj = [j for j in item_objs if j.itemid == i][0]
        opponent_obj = [m for m in monster_objs if m.id == opponent][0]
        win_obj = [j for j in item_objs if j.itemid == trade][0]
        f.seek(pointer + (i*4))
        f.write(chr(opponent))
        f.seek(pointer + (i*4) + 2)
        f.write(chr(trade))

        if abs(wager_obj.rank() - win_obj.rank()) >= 5000 and random.randint(1, 2) == 2:
            f.write(chr(0xFF))
        else:
            f.write(chr(0x00))

    f.close()


def randomize_slots(filename, pointer):
    spells = get_ranked_spells(filename)
    spells = [s.spellid for s in spells if s.target_enemy_default]
    f = open(filename, 'r+b')
    for i in xrange(7):
        if i == 2:
            continue
        else:
            if i in [1, 2, 4]:
                index = random.randint(len(spells)/2, len(spells)-1)
            else:
                index = random.randint(0, len(spells)-1)
            value = spells[index]
        f.seek(pointer+i)
        f.write(chr(value))
    f.close()


def manage_commands(commands, characters):
    alrs = AutoLearnRageSub(require_gau=False)
    alrs.set_location(0x23b73)
    alrs.write(outfile)

    args = AutoRecruitGauSub()
    args.set_location(0xcfe1a)
    args.write(outfile)

    recruit_gau_sub = Substitution()
    recruit_gau_sub.bytestring = [0x89, 0xFF]
    recruit_gau_sub.set_location(0x24856)
    recruit_gau_sub.write(outfile)

    learn_lore_sub = Substitution()
    learn_lore_sub.bytestring = [0xEA, 0xEA, 0xF4, 0x00, 0x00, 0xF4, 0x00,
                                 0x00]
    learn_lore_sub.set_location(0x236E4)
    learn_lore_sub.write(outfile)

    learn_dance_sub = Substitution()
    learn_dance_sub.bytestring = [0xEA] * 2
    learn_dance_sub.set_location(0x25EE8)
    learn_dance_sub.write(outfile)

    learn_swdtech_sub = Substitution()
    learn_swdtech_sub.bytestring = [0xEA] * 2
    learn_swdtech_sub.set_location(0x261C9)
    learn_swdtech_sub.write(outfile)
    learn_swdtech_sub.bytestring = [0x4C, 0xDA, 0xA1, 0xEA]
    learn_swdtech_sub.set_location(0xA18A)
    learn_swdtech_sub.write(outfile)

    learn_blitz_sub = Substitution()
    learn_blitz_sub.bytestring = [0xEA] * 2
    learn_blitz_sub.set_location(0x261E5)
    learn_blitz_sub.write(outfile)
    learn_blitz_sub.bytestring = [0xEA] * 4
    learn_blitz_sub.set_location(0xA18E)
    learn_blitz_sub.write(outfile)

    learn_multiple_sub = Substitution()
    learn_multiple_sub.set_location(0xA1B4)
    reljump = 0xFE - (learn_multiple_sub.location - 0xA186)
    learn_multiple_sub.bytestring = [0xF0, reljump]
    learn_multiple_sub.write(outfile)

    learn_multiple_sub.set_location(0xA1D6)
    reljump = 0xFE - (learn_multiple_sub.location - 0xA18A)
    learn_multiple_sub.bytestring = [0xF0, reljump]
    learn_multiple_sub.write(outfile)

    learn_multiple_sub.set_location(0xA200)
    learn_multiple_sub.bytestring = [0xEA]
    learn_multiple_sub.write(outfile)

    learn_multiple_sub.set_location(0x261DD)
    learn_multiple_sub.bytestring = [0xEA] * 3
    learn_multiple_sub.write(outfile)

    rage_blank_sub = Substitution()
    rage_blank_sub.bytestring = [0x01] + ([0x00] * 31)
    rage_blank_sub.set_location(0x47AA0)
    rage_blank_sub.write(outfile)

    eems = EnableEsperMagicSub()
    eems.set_location(0x3F091)
    eems.write(outfile)

    # Prevent Runic, SwdTech, and Capture from being disabled/altered
    protect_battle_commands_sub = Substitution()
    protect_battle_commands_sub.bytestring = [0x03, 0xFF, 0xFF, 0x0C,
                                              0x17, 0x02, 0xFF, 0x00]
    protect_battle_commands_sub.set_location(0x252E9)
    protect_battle_commands_sub.write(outfile)

    enable_morph_sub = Substitution()
    enable_morph_sub.bytestring = [0xEA] * 2
    enable_morph_sub.set_location(0x25410)
    enable_morph_sub.write(outfile)

    enable_mpoint_sub = Substitution()
    enable_mpoint_sub.bytestring = [0xEA] * 2
    enable_mpoint_sub.set_location(0x25E38)
    enable_mpoint_sub.write(outfile)

    ungray_statscreen_sub = Substitution()
    ungray_statscreen_sub.bytestring = [0x20, 0x6F, 0x61, 0x30, 0x26, 0xEA,
                                        0xEA, 0xEA]
    ungray_statscreen_sub.set_location(0x35EE1)
    ungray_statscreen_sub.write(outfile)

    locke_scenario_sub = Substitution()
    locke_scenario_sub.bytestring = [0x9D, 0xAB]
    locke_scenario_sub.set_location(0x42AC4)
    locke_scenario_sub.write(outfile)

    invalid_commands = ["fight", "item", "magic", "xmagic",
                        "def", "row", "summon", "revert"]
    if random.randint(1, 5) != 5:
        invalid_commands.append("magitek")
    invalid_commands = set([c for c in commands.values() if c.name in invalid_commands])

    def populate_unused():
        unused_commands = set(commands.values())
        unused_commands = list(unused_commands - invalid_commands)
        return sorted(unused_commands, key=lambda c: c.name)

    unused = populate_unused()
    xmagic_taken = False
    random.shuffle(characters)
    for c in characters:
        if c.id <= 11:
            using = []
            while not using:
                if random.randint(0, 1):
                    using.append(commands["item"])
                if random.randint(0, 1):
                    if not xmagic_taken:
                        using.append(commands["xmagic"])
                        xmagic_taken = True
                    else:
                        using.append(commands["magic"])
            while len(using) < 3:
                if not unused:
                    unused = populate_unused()
                com = random.choice(unused)
                unused.remove(com)
                if com not in using:
                    using.append(com)
                    if com.name == "morph":
                        invalid_commands.add(com)
                        morph_char_sub = Substitution()
                        morph_char_sub.bytestring = [0xC9, c.id]
                        morph_char_sub.set_location(0x25E32)
                        morph_char_sub.write(outfile)
            for i, command in enumerate(reversed(using)):
                c.set_battle_command(i+1, command=command)
            if c.id == 11:
                # Fixing Gau
                c.set_battle_command(0, commands["fight"])
        else:
            c.set_battle_command(1, command_id=0xFF)
            c.set_battle_command(2, command_id=0xFF)
        c.write_battle_commands(outfile)

    magitek_skills = [SpellBlock(i, sourcefile) for i in xrange(0x83, 0x8B)]
    for ms in magitek_skills:
        ms.fix_reflect(outfile)

    return commands, characters


def manage_commands_new(commands, characters):
    # note: x-magic targets random party member
    # replacing lore screws up enemy skills
    # replacing jump makes the character never come back down
    # replacing mimic screws up enemy skills too
    freespaces = []
    freespaces.append(FreeBlock(0x2A65A, 0x2A800))
    freespaces.append(FreeBlock(0x2FAAC, 0x2FC6D))

    valid = set(list(commands))
    valid = sorted(valid - set(["row", "def"]))
    used = []
    all_spells = [SpellBlock(i, sourcefile) for i in xrange(0xFF)]
    for c in commands.values():
        if c.name in NEVER_REPLACE:
            continue

        if c.name not in ALWAYS_REPLACE:
            if random.randint(1, 100) > 75:
                continue
            if c.target == "self" and random.randint(1, 100) > 50:
                continue

        POWER_LEVEL = 100
        while True:
            power = POWER_LEVEL / 2
            while True:
                power += random.randint(0, POWER_LEVEL)
                if random.choice([True, False]):
                    break

            def spell_is_valid(s):
                if not s.valid:
                    return False
                if s.spellid in used:
                    return False
                if not c.restriction(s):
                    return False
                return s.rank() <= power

            valid_spells = filter(spell_is_valid, all_spells)
            if not valid_spells:
                continue

            sb = random.choice(valid_spells)
            used.append(sb.spellid)
            c.set_retarget(sb, outfile)
            s = SpellSub(spellid=sb.spellid)
            break

        myfs = None
        for fs in freespaces:
            if fs.size > s.size:
                myfs = fs
                break

        freespaces.remove(myfs)
        s.set_location(myfs.start)
        s.write(outfile)
        c.setpointer(s.location, outfile)
        fss = myfs.unfree(s.location, s.size)
        freespaces.extend(fss)

        c.newname(sb.name, outfile)
        c.unsetmenu(outfile)

    gogo_enable_all_sub = Substitution()
    gogo_enable_all_sub.bytestring = [0xEA] * 2
    gogo_enable_all_sub.set_location(0x35E58)
    gogo_enable_all_sub.write(outfile)

    ai_command_allow = Substitution()
    ai_command_allow.bytestring = [0xED, 0x3E, 0xDF, 0x3D]
    ai_command_allow.set_location(0x204D0)
    ai_command_allow.write(outfile)

    cyan_ai_sub = Substitution()
    cyan_ai_sub.bytestring = [0xF0, 0xEE, 0xEE, 0xEE, 0xFF]
    cyan_ai_sub.set_location(0xFBE85)
    cyan_ai_sub.write(outfile)

    return commands, characters


def manage_natural_magic(characters):
    candidates = [c for c in characters if 0x02 in c.battle_commands or
                  0x17 in c.battle_commands]
    candidates = random.sample(candidates, 2)
    natmag_learn_sub = Substitution()
    natmag_learn_sub.bytestring = [0xC9, candidates[0].id]
    natmag_learn_sub.set_location(0x261B9)
    natmag_learn_sub.write(outfile)
    natmag_learn_sub.set_location(0xA182)
    natmag_learn_sub.write(outfile)
    address = 0x1A6E + (54 * candidates[0].id)
    natmag_learn_sub.bytestring = [0x99, address & 0xFF, address >> 8]
    natmag_learn_sub.set_location(0xA1AB)
    natmag_learn_sub.write(outfile)

    natmag_learn_sub.bytestring = [0xC9, candidates[1].id]
    natmag_learn_sub.set_location(0x261C0)
    natmag_learn_sub.write(outfile)
    natmag_learn_sub.set_location(0xA186)
    natmag_learn_sub.write(outfile)
    address = 0x1A6E + (54 * candidates[1].id)
    natmag_learn_sub.bytestring = [0x99, address & 0xFF, address >> 8]
    natmag_learn_sub.set_location(0xA1CD)
    natmag_learn_sub.write(outfile)

    spells = get_ranked_spells(sourcefile, magic_only=True)
    spellids = [s.spellid for s in spells]
    f = open(outfile, 'r+b')
    address = 0x2CE3C0

    def mutate_spell(pointer, used):
        f.seek(pointer)
        spell, level = tuple(map(ord, f.read(2)))

        while True:
            index = spellids.index(spell)
            levdex = int((level / 99.0) * len(spellids))
            a, b = min(index, levdex), max(index, levdex)
            index = random.randint(a, b)
            index += random.randint(-3, 3)
            index = max(0, min(index, len(spells)-1))
            while random.choice([True, False]):
                index += random.randint(-1, 1)
                index = max(0, min(index, len(spells)-1))

            level += random.randint(-2, 2)
            level = max(1, min(level, 99))
            while random.choice([True, False]):
                level += random.randint(-1, 1)
                level = max(0, min(level, 99))

            newspell = spellids[index]
            if newspell in used:
                continue
            break

        used.append(newspell)
        f.seek(pointer)
        f.write(chr(newspell))
        f.write(chr(level))

    usedspells = []
    for i in xrange(16):
        pointer = address + (2*i)
        mutate_spell(pointer, usedspells)

    usedspells = []
    for i in xrange(16):
        pointer = address + 32 + (2*i)
        mutate_spell(pointer, usedspells)

    lores = get_ranked_spells(sourcefile, magic_only=False)
    lores = filter(lambda s: 0x8B <= s.spellid <= 0xA2, lores)
    lore_ids = [l.spellid for l in lores]
    lores_in_order = sorted(lore_ids)
    address = 0x26F564
    f.seek(address)
    known_lores = read_multi(f, length=3)
    known_lore_ids = []
    for i in xrange(24):
        if (1 << i) & known_lores:
            known_lore_ids.append(lores_in_order[i])

    new_known_lores = 0
    random.shuffle(known_lore_ids)
    for lore_id in known_lore_ids:
        if new_known_lores and random.choice([True, False]):
            continue

        index = lore_ids.index(lore_id)
        index += random.randint(-4, 2)
        index = max(0, min(index, len(lores)-1))
        while random.choice([True, False]):
            index += random.randint(-2, 1)
            index = max(0, min(index, len(lores)-1))
        new_lore = lores[index]
        order = lores_in_order.index(new_lore.spellid)
        new_known_lores |= (1 << order)

    f.seek(address)
    write_multi(f, new_known_lores, length=3)
    f.close()

    return candidates


def manage_umaro(characters):
    # ship unequip - cc3510
    equip_umaro_sub = Substitution()
    equip_umaro_sub.bytestring = [0xC9, 0x0E]
    equip_umaro_sub.set_location(0x31E6E)
    equip_umaro_sub.write(outfile)
    equip_umaro_sub.bytestring = [0xEA] * 2
    equip_umaro_sub.set_location(0x39EF6)
    equip_umaro_sub.write(outfile)

    umaro_risk = random.randint(0, 13)
    umaro_risk = [c for c in characters if c.id == umaro_risk][0]
    umaro = [c for c in characters if c.id == 13][0]
    umaro.battle_commands = list(umaro_risk.battle_commands)
    umaro_risk.battle_commands = [None, 0xFF, 0xFF, 0xFF]

    umaro.beserk = False
    umaro_risk.beserk = True

    umaro_risk.write_battle_commands(outfile)
    umaro.write_battle_commands(outfile)

    umaro_exchange_sub = Substitution()
    umaro_exchange_sub.bytestring = [0xC9, umaro_risk.id]
    umaro_exchange_sub.set_location(0x21617)
    umaro_exchange_sub.write(outfile)
    umaro_exchange_sub.set_location(0x20926)
    umaro_exchange_sub.write(outfile)

    spells = get_ranked_spells(sourcefile)
    spells = filter(lambda x: x.target_enemy_default, spells)
    spells = filter(lambda x: x.valid, spells)
    spells = filter(lambda x: x.rank() < 1000, spells)
    spell_ids = [s.spellid for s in spells]
    index = spell_ids.index(0x54)  # storm
    index += random.randint(0, 10)
    while random.choice([True, False]):
        index += random.randint(-10, 10)
    index = max(0, min(index, len(spell_ids)-1))
    spell_id = spell_ids[index]
    storm_sub = Substitution()
    storm_sub.bytestring = [0xA9, spell_id]
    storm_sub.set_location(0x21710)
    storm_sub.write(outfile)

    return umaro_risk


def manage_sprint():
    autosprint = Substitution()
    autosprint.set_location(0x4E2D)
    autosprint.bytestring = [0x80, 0x00]
    autosprint.write(outfile)


def manage_skips():
    intro_skip_sub = Substitution()
    intro_skip_sub.bytestring = [0xFD] * 4
    intro_skip_sub.set_location(0xA5E8E)
    intro_skip_sub.write(outfile)

    flashback_skip_sub = Substitution()
    flashback_skip_sub.bytestring = [0xB2, 0xB8, 0xA5, 0x00, 0xFE]
    flashback_skip_sub.set_location(0xAC582)
    flashback_skip_sub.write(outfile)

    boat_skip_sub = Substitution()
    boat_skip_sub.bytestring = (
        [0x97, 0x5C] +
        [0xD0, 0x87] +
        [0x3D, 0x03, 0x3F, 0x03, 0x01] +
        [0x6B, 0x00, 0x04, 0xE8, 0x96, 0x40, 0xFF]
        )
    boat_skip_sub.set_location(0xC615A)
    boat_skip_sub.write(outfile)

    leo_skip_sub = Substitution()
    leo_skip_sub.bytestring = (
        [0x97, 0x5C] +
        [0xDB, 0xF7, 0xD5, 0xF2, 0xD5, 0xF3, 0xD5, 0xF4, 0xD5, 0xF5, 0xD5, 0xF9, 0xD5, 0xFB, 0xD5, 0xF6] +
        [0x77, 0x02, 0x77, 0x03, 0x77, 0x04, 0x77, 0x05, 0x77, 0x09, 0x77, 0x0B, 0x77, 0x06] +
        [0xD4, 0xF2, 0xD4, 0xF4, 0xD4, 0xF5, 0xD4, 0xF9, 0xD4, 0xFB, 0xD4, 0xF6] +
        [0xB2, 0x35, 0x09, 0x02] +
        [0xD3, 0xCC, 0xD0, 0x9D, 0xD2, 0xBA, 0xDA, 0x5A, 0xDA, 0xD9, 0xDB, 0x20, 0xDA, 0x68] +
        [0xD2, 0xB3, 0xD2, 0xB4] +
        [0xD0, 0x7A] +
        [0xD2, 0x76, 0xD2, 0x6F] +
        [0x6B, 0x00, 0x04, 0xF9, 0x80, 0x00] +
        [0xC7, 0xF9, 0x7F, 0xFF]
        )
    leo_skip_sub.set_location(0xBF2B5)
    leo_skip_sub.write(outfile)

    shadow_leaving_sub = Substitution()
    shadow_leaving_sub.bytestring = [0xEA] * 2
    shadow_leaving_sub.set_location(0x2488A)
    shadow_leaving_sub.write(outfile)

    narshe_skip_sub = Substitution()
    narshe_skip_sub.bytestring = []
    narshe_skip_sub.bytestring += [0x3E, 0x0D, 0x3D, 0x00, 0x3D, 0x04,
                                   0x3D, 0x0E, 0x3D, 0x05, 0x3D, 0x02,
                                   0x3D, 0x0B, 0x3D, 0x01, 0x3D, 0x06]
    narshe_skip_sub.bytestring += [0xD2, 0xCC, 0xD4, 0xBC]
    narshe_skip_sub.bytestring += [0x3F, 0x00, 0x01, 0x3F, 0x0D, 0x00]
    address = 0x2BC44 - len(narshe_skip_sub.bytestring)
    narshe_skip_sub.set_location(address + 0xA0000)
    narshe_skip_sub.write(outfile)
    narshe_skip_sub.bytestring = [0xB2, address & 0xFF, (address >> 8) & 0xFF, address >> 16]
    narshe_skip_sub.set_location(0xAADC4)
    narshe_skip_sub.write(outfile)


def manage_balance():
    vanish_doom_sub = Substitution()
    vanish_doom_sub.bytestring = [
        0xAD, 0xA2, 0x11, 0x89, 0x02, 0xF0, 0x07, 0xB9, 0xA1, 0x3A, 0x89, 0x04,
        0xD0, 0x6E, 0xA5, 0xB3, 0x10, 0x1C, 0xB9, 0xE4, 0x3E, 0x89, 0x10, 0xF0,
        0x15, 0xAD, 0xA4, 0x11, 0x0A, 0x30, 0x07, 0xAD, 0xA2, 0x11, 0x4A, 0x4C,
        0xB3, 0x22, 0xB9, 0xFC, 0x3D, 0x09, 0x10, 0x99, 0xFC, 0x3D, 0xAD, 0xA3,
        0x11, 0x89, 0x02, 0xD0, 0x0F, 0xB9, 0xF8, 0x3E, 0x10, 0x0A, 0xC2, 0x20,
        0xB9, 0x18, 0x30, 0x04, 0xA6, 0x4C, 0xE5, 0x22
        ]
    vanish_doom_sub.set_location(0x22215)
    vanish_doom_sub.write(outfile)

    evade_mblock_sub = Substitution()
    evade_mblock_sub.bytestring = [
        0xF0, 0x17, 0x20, 0x5A, 0x4B, 0xC9, 0x40, 0xB0, 0x9C, 0xB9, 0xFD, 0x3D,
        0x09, 0x04, 0x99, 0xFD, 0x3D, 0x80, 0x92, 0xB9, 0x55, 0x3B, 0x48,
        0x80, 0x43, 0xB9, 0x54, 0x3B, 0x48, 0xEA
        ]
    evade_mblock_sub.set_location(0x2232C)
    evade_mblock_sub.write(outfile)

    randomize_slots(outfile, 0x24E4A)


def manage_monsters():
    monsters = monsters_from_table(ENEMY_TABLE)
    for m in monsters:
        m.read_stats(sourcefile)
        m.screw_vargas()
        m.mutate()

    shuffle_monsters(monsters)
    for m in monsters:
        m.write_stats(outfile)

    return monsters


def manage_monster_appearance(monsters):
    mgs = []
    for j, m in enumerate(monsters):
        mg = MonsterGraphicBlock(pointer=0x127000 + (5*j), name=m.name)
        mg.read_data(sourcefile)
        m.set_graphics(graphics=mg)
        mgs.append(mg)

    for m in monsters:
        pp = m.graphics.palette_pointer
        others = [i for i in monsters if i.graphics.palette_pointer == pp + 0x10]
        if others:
            m.graphics.palette_data = m.graphics.palette_data[:0x10]

    nonbosses = [m for m in monsters if not m.is_boss]
    bosses = [m for m in monsters if m.is_boss]
    nonbossgraphics = [m.graphics.graphics for m in nonbosses]
    bosses = [m for m in bosses if m.graphics.graphics not in nonbossgraphics]

    get_formations(sourcefile)
    for i, m in enumerate(nonbosses):
        if "Chupon" in m.name:
            m.update_pos(6, 6)
            m.update_size(8, 16)
        if "Siegfried" in m.name:
            m.update_pos(8, 8)
            m.update_size(8, 8)
        candidates = nonbosses[i:]
        m.mutate_graphics_swap(candidates)
        randomize_enemy_name(outfile, m.id)

    done = {}
    freepointer = 0x127820
    for m in monsters:
        mg = m.graphics
        idpair = (m.name, mg.palette_pointer)
        if idpair not in done:
            done[idpair] = freepointer
            freepointer += len(mg.palette_data)
        mg.mutate_palette()
        mg.write_data(outfile, palette_pointer=done[idpair])


def manage_items(items):
    for i in items:
        i.mutate()
        i.unrestrict()
        i.write_stats(outfile)

    return items


def manage_equipment(items, characters):
    reset_equippable(items)
    for c in characters:
        if c.id > 13:
            continue

        equippable_items = filter(lambda i: i.equippable & (1 << c.id), items)
        equippable_items = filter(lambda i: not i.has_disabling_status, equippable_items)
        if random.randint(1, 4) < 4:
            equippable_items = filter(lambda i: not i.imp_only, equippable_items)
        equippable_dict = {"weapon": lambda i: i.is_weapon,
                           "shield": lambda i: i.is_shield,
                           "helm": lambda i: i.is_helm,
                           "armor": lambda i: i.is_body_armor}

        for equiptype, func in equippable_dict.items():
            equippable = filter(func, equippable_items)
            weakest = 0xFF
            if equippable:
                weakest = min(equippable, key=lambda i: i.rank()).itemid
            c.write_default_equipment(outfile, weakest, equiptype)

    for i in items:
        i.write_stats(outfile)

    return items, characters


def manage_espers():
    espers = espers_from_table(ESPER_TABLE)
    random.shuffle(espers)
    for e in espers:
        e.read_data(sourcefile)
        e.generate_spells()
        e.generate_bonus()
        e.write_data(outfile)

    return espers


def manage_treasure(monsters):
    chests = chests_from_table(CHEST_TABLE)
    for c in chests:
        c.read_data(sourcefile)
        c.mutate_contents()

    shuffle_locations(chests)
    shuffle_monster_boxes(chests)

    for c in chests:
        c.write_data(outfile)

    randomize_colosseum(outfile, 0x1fb600)

    for i in range(26):
        address = 0x47f40 + (i*4)
        mm = MetamorphBlock(pointer=address)
        mm.read_data(sourcefile)
        mm.mutate_items()
        mm.write_data(outfile)

    for m in monsters:
        m.mutate_items()
        m.mutate_metamorph()
        m.write_stats(outfile)

    return chests


def manage_blitz():
    blitzspecptr = 0x47a40
    adjacency = {0x7: [0xE, 0x8],
                 0x8: [0x7, 0x9],
                 0x9: [0x8, 0xA],
                 0xA: [0x9, 0xB],
                 0xB: [0xA, 0xC],
                 0xC: [0xB, 0xD],
                 0xD: [0xC, 0xE],
                 0xE: [0xD, 0x7]}
    f = open(outfile, 'r+b')
    for i in xrange(1, 8):
        # skip pummel
        current = blitzspecptr + (i * 12)
        f.seek(current + 11)
        length = ord(f.read(1)) / 2
        newlength = random.randint(1, length) + random.randint(0, length)
        newlength = min(newlength, 10)

        newcmd = []
        while len(newcmd) < newlength:
            prev = newcmd[-1] if newcmd else None
            pprev = newcmd[-2] if len(newcmd) > 1 else None
            if (prev and prev in adjacency and random.randint(1, 3) != 3):
                nextin = random.choice(adjacency[prev])
                if nextin == pprev and random.randint(1, 4) != 4:
                    nextin = [i for i in adjacency[prev] if i != nextin][0]
                newcmd.append(nextin)
            else:
                if random.choice([True, False]):
                    newcmd.append(random.randint(0x07, 0x0E))
                else:
                    newcmd.append(random.randint(0x03, 0x06))

        newcmd += [0x01]
        while len(newcmd) < 11:
            newcmd += [0x00]
        newcmd += [(newlength+1) * 2]
        f.seek(current)
        f.write("".join(map(chr, newcmd)))


def manage_formations():
    unused_enemies = [u for u in monsters if u.id in REPLACE_ENEMIES]
    unused_formations = [u for u in formations if set(u.enemies) & set(unused_enemies)]
    unused_formations += [u for u in formations if u.formid in REPLACE_FORMATIONS]
    boss_formations = [fo for fo in formations if fo.formid not in unused_formations]
    single_boss_formations = list(boss_formations)
    single_boss_formations = [bf for bf in single_boss_formations if len(bf.present_enemies) == 1]
    single_boss_formations = [bf for bf in single_boss_formations if bf.formid not in REPLACE_FORMATIONS]
    single_boss_formations = [bf for bf in single_boss_formations if bf.present_enemies[0].graphics.large or bf.present_enemies[0].boss_death]
    boss_formations = [fo for fo in boss_formations if any([m.boss_death for m in fo.present_enemies])]

    safe_boss_formations = list(boss_formations)
    safe_boss_formations = [fo for fo in safe_boss_formations if not any([m.battle_event for m in fo.present_enemies])]

    bosses = sorted([m for m in monsters if m.boss_death], key=lambda m: m.stats['level'])
    repurposed_formations = []
    used_graphics = []
    for ue, uf in zip(unused_enemies, unused_formations):
        while True:
            vbf = random.choice(single_boss_formations)
            vboss = [e for e in vbf.enemies if e][0]
            if vboss.graphics.graphics not in used_graphics:
                used_graphics.append(vboss.graphics.graphics)
                break
        ue.graphics.copy_data(vboss.graphics)
        uf.copy_data(vbf)
        uf.lookup_enemies(monsterdict)
        eids = []
        for eid in uf.enemy_ids:
            if eid & 0xFF == vboss.id & 0xFF:
                eids.append(ue.id)
            else:
                eids.append(eid)
        uf.set_big_enemy_ids(eids)
        uf.lookup_enemies(monsterdict)

        bf = random.choice(safe_boss_formations)
        boss = random.choice([e for e in bf.present_enemies if e.boss_death])
        ue.copy_all(boss, everything=True)
        index = bosses.index(boss)
        index += random.randint(-3, 3)
        index = max(0, min(index, len(bosses)-1))
        while random.choice([True, False]):
            index += random.randint(-2, 2)
            index = max(0, min(index, len(bosses)-1))
        boss2 = bosses[index]
        ue.copy_all(boss2, everything=False)
        ue.stats['level'] = max(boss.stats['level'], boss2.stats['level'])
        ue.read_ai(outfile)
        assert ue.boss_death
        ue.mutate()
        ue.treasure_boost()
        ue.graphics.mutate_palette()
        randomize_enemy_name(outfile, ue.id)
        ue.write_stats(outfile)

        uf.set_music_appropriate()
        appearances = range(1, 14)
        if ue.stats['level'] > 50:
            appearances += [15]
        uf.set_appearing(random.choice(appearances))
        ue.graphics.write_data(outfile)
        uf.mouldbyte = 0x60
        uf.write_data(outfile)
        repurposed_formations.append(uf)

    rare_candidates = repurposed_formations + safe_boss_formations
    random.shuffle(fsets)
    for fs in fsets:
        chosen = fs.mutate_formations(rare_candidates, extreme=False, verbose=False)
        if chosen:
            if chosen.misc3 & 0b00111000 == 0:
                chosen.set_music(1)
                chosen.write_data(outfile)
            chosen = chosen.present_enemies[0]
            rare_candidates = [rc for rc in rare_candidates if rc.present_enemies[0].name != chosen.name]
        fs.write_data(outfile)


def manage_shops():
    for i in xrange(0x80):
        pointer = 0x47AC0 + (9*i)
        s = ShopBlock(pointer)
        s.read_data(sourcefile)
        s.mutate_misc()
        s.mutate_items(outfile)
        s.write_data(outfile)


def randomize_enemy_name(filename, enemy_id):
    pointer = 0xFC050 + (enemy_id * 10)
    f = open(filename, 'r+b')
    f.seek(pointer)
    name = generate_name()
    name = map(lambda c: hex2int(texttable[c]), name)
    while len(name) < 10:
        name.append(0xFF)
    f.write("".join(map(chr, name)))
    f.close()


if __name__ == "__main__":
    if len(argv) > 2:
        sourcefile = argv[1].strip()
    else:
        sourcefile = raw_input("Path to rom file? ").strip()

    f = open(sourcefile, 'rb')
    h = md5(f.read()).hexdigest()
    if h != MD5HASH:
        print ("WARNING! The md5 hash of this file does not match the known "
               "hash of the english FF6 1.0 rom!")
    f.close()

    if len(argv) > 2:
        fullseed = argv[2].strip()
    else:
        fullseed = raw_input("Seed? ").strip()
        if '.' not in fullseed:
            fullseed = "..%s" % fullseed

    version, flags, seed = tuple(fullseed.split('.'))
    seed = seed.strip()
    if not seed:
        seed = int(time())
    else:
        seed = int(seed)
    random.seed(seed)

    if version and version != VERSION:
        print ("WARNING! Version mismatch! "
               "This seed will not produce the expected result!")
    print "Using seed: %s.%s.%s" % (VERSION, flags, seed)

    if 'v' in flags:
        VERBOSE = True
        flags = "".join([c for c in flags if c != 'v'])

    flags = flags.lower()
    if not flags.strip():
        flags = 'abcdefghijklmnopqrstuvwxyz'

    outfile = sourcefile.rsplit('.', 1)
    outfile = '.'.join([outfile[0], str(seed), outfile[1]])
    copyfile(sourcefile, outfile)

    commands = commands_from_table(COMMAND_TABLE)
    commands = dict([(c.name, c) for c in commands])

    characters = characters_from_table(CHAR_TABLE)

    if 'o' in flags:
        manage_commands(commands, characters)

    if 'w' in flags:
        manage_commands_new(commands, characters)

    if 'z' in flags:
        manage_sprint()

    if 's' in flags:
        manage_skips()

    if 'b' in flags:
        manage_balance()

    if 'm' in flags:
        monsters = manage_monsters()
    else:
        monsters = monsters_from_table(ENEMY_TABLE)
        for m in monsters:
            m.read_stats(sourcefile)

    if 'c' in flags:
        manage_monster_appearance(monsters)

    formations = get_formations(sourcefile)

    fsets = []
    for i in xrange(256):
        fs = FormationSet(setid=i)
        fs.read_data(sourcefile)
        fs.set_formations(formations)
        fs.shuffle_formations()
        fsets.append(fs)

    ranked_fsets = sorted(fsets, key=lambda fs: fs.rank())
    for a, b in zip(ranked_fsets, ranked_fsets[1:]):
        a.swap_formations(b)

    for m in monsters:
        m.read_ai(outfile)
    items = get_ranked_items(sourcefile)
    if 'i' in flags:
        manage_items(items)

    if 'q' in flags:
        # do this after items
        manage_equipment(items, characters)

    if 'e' in flags:
        manage_espers()

    if 't' in flags:
        manage_treasure(monsters)

    if 'p' in flags:
        # do this after items
        manage_shops()

    if 'u' in flags:
        umaro_risk = manage_umaro(characters)
        reset_rage_blizzard(items, umaro_risk, outfile)

    if 'q' in flags:
        # do this after swapping beserk
        reset_special_relics(items, characters, outfile)

        for c in characters:
            c.mutate_stats(outfile)

    if 'o' in flags:
        # do this after swapping beserk
        natmag_candidates = manage_natural_magic(characters)
    else:
        natmag_candidates = None

    if 'l' in flags:
        manage_blitz()

    if 'f' in flags:
        manage_formations()

    if VERBOSE:
        for c in sorted(characters, key=lambda c: c.id):
            if c.id > 13:
                continue

            ms = [m for m in c.battle_commands if m]
            ms = [filter(lambda x: x.id == m, commands.values()) for m in ms]
            print "%s:" % c.name,
            for m in ms:
                if m:
                    print m[0].name.lower(),
            print
        if natmag_candidates:
            natmag_candidates = tuple(nc.name for nc in natmag_candidates)
            print "Natural magic: %s %s" % natmag_candidates
