' The TV screen. Everything here only SHOWS what the board sent; nothing is sent back.
sub init()
    m.C = {
        bg: "0x04101FFF", panel: "0x071A30FF", line: "0x164060FF", white: "0xF2F2F2FF",
        dim: "0x8FA6BCFF", green: "0x3EE07AFF", red: "0xFF5C52FF", cyan: "0x5CC8DBFF",
        amber: "0xF2B84BFF", sym: "0x4FB86AFF", box: "0x04101FFF",
        upbg: "0x0D2E37FF", downbg: "0x221B2EFF", flashUp: "0x3EE07A66", flashDown: "0xFF5C5266"
    }
    m.fonts = {}
    m.page = m.top.findNode("page")
    m.top.backgroundColor = m.C.bg
    m.top.backgroundURI = ""

    ' a hidden, empty video player keeps the Roku screensaver from covering the board
    m.keep = m.top.createChild("Video")
    m.keep.visible = false
    m.keep.disableScreenSaver = true

    ' price flashes fade after half a second
    m.flashTimer = m.top.createChild("Timer")
    m.flashTimer.duration = 0.5
    m.flashTimer.observeField("fire", "endFlash")

    m.tiles = []
    m.tileGrid = invalid
    m.shape = ""
    buildFrame()
    showMessage("Connecting to the board...")

    m.feed = CreateObject("roSGNode", "FeedTask")
    m.feed.observeField("result", "onResult")
    m.feed.observeField("tiles", "onTiles")
    m.feed.observeField("problem", "onProblem")
    m.feed.control = "RUN"
    chInit()          ' Alex's channel (ChannelView.brs): right arrow
    m.top.setFocus(true)        ' the remote's buttons come to this screen
end sub

' ------------------------------------------------------------ small helpers
function font(size as integer, bold as boolean) as object
    k = size.ToStr()
    if bold then k = k + "b"
    if m.fonts[k] = invalid
        f = CreateObject("roSGNode", "Font")
        if bold then f.uri = "font:BoldSystemFontFile" else f.uri = "font:SystemFontFile"
        f.size = size
        m.fonts[k] = f
    end if
    return m.fonts[k]
end function

function lbl(parent as object, text as string, x as integer, y as integer, w as integer, size as integer, bold as boolean, color as string, align = "left" as string) as object
    l = parent.createChild("Label")
    l.translation = [x, y]
    l.width = w
    l.horizAlign = align
    l.font = font(size, bold)
    l.color = color
    l.text = text
    return l
end function

function rect(parent as object, x as integer, y as integer, w as integer, h as integer, color as string) as object
    r = parent.createChild("Rectangle")
    r.translation = [x, y]
    r.width = w
    r.height = h
    r.color = color
    return r
end function

function dirColor(d as dynamic) as string
    if d = invalid then return m.C.white
    if d > 0 then return m.C.green
    if d < 0 then return m.C.red
    return m.C.white
end function

function txt(v as dynamic) as string
    if v = invalid then return ""
    if type(v) = "roString" or type(v) = "String" then return v
    return v.ToStr()
end function

function panel(x as integer, y as integer, w as integer, h as integer, title as string, note as string) as object
    g = m.page.createChild("Group")
    g.translation = [x, y]
    rect(g, 0, 0, w, h, m.C.line)
    rect(g, 2, 2, w - 4, h - 4, m.C.panel)
    t = lbl(g, UCase(title), 28, 16, w - 56, 24, true, m.C.cyan)
    n = lbl(g, note, 28, 18, w - 56, 20, false, m.C.dim, "right")
    return { g: g, title: t, note: n }
end function

function mix(a as string, b as string, t as float) as string
    out = "0x"
    for i = 0 to 2
        x = Val(Mid(a, i * 2 + 1, 2), 16)
        y = Val(Mid(b, i * 2 + 1, 2), 16)
        v = Int(x + (y - x) * t + 0.5)
        h = StrI(v, 16)
        if Len(h) < 2 then h = "0" + h
        out = out + UCase(h)
    end for
    return out + "FF"
end function

sub clearRows(list as object)
    for each n in list
        n.getParent().removeChild(n)
    end for
    list.clear()
end sub

sub showMessage(text as string)
    m.message.text = text
end sub

' ------------------------------------------------------------ the fixed parts of the screen
sub buildFrame()
    p = m.page
    ' header
    rect(p, 80, 34, 76, 76, m.C.cyan)
    rect(p, 83, 37, 70, 70, m.C.bg)
    lbl(p, "AH", 80, 51, 76, 34, true, m.C.cyan, "center")
    lbl(p, "ALEX HALE'S BOARD", 180, 36, 900, 46, true, m.C.white)
    lbl(p, "Read only  ·  OK = reconnect  ·  right arrow = Alex's channel", 182, 88, 640, 22, false, m.C.dim)
    m.status = lbl(p, "", 830, 88, 440, 22, false, m.C.dim)
    m.clock = lbl(p, "", 1240, 32, 600, 52, true, m.C.white, "right")
    m.session = lbl(p, "", 1040, 90, 800, 26, false, m.C.dim, "right")

    ' row 1: mood meters
    m.meters = []
    mw = 570
    for i = 0 to 2
        x = 80 + i * (mw + 25)
        pn = panel(x, 124, mw, 112, "", "")
        g = pn.g
        score = lbl(g, "--", 28, 40, 160, 40, true, m.C.white)
        word = lbl(g, "", 160, 50, mw - 188, 24, true, m.C.white)
        ' red to green bar, 24 steps
        for s = 0 to 23
            t = s / 23.0
            if t < 0.5
                c = mix("FF5C52", "F2B84B", t * 2)
            else
                c = mix("F2B84B", "3EE07A", (t - 0.5) * 2)
            end if
            rect(g, 28 + s * 21, 90, 21, 8, c)
        end for
        needle = rect(g, 28, 84, 6, 20, m.C.white)
        m.meters.push({ title: pn.title, note: pn.note, score: score, word: word, needle: needle })
    end for

    ' row 2 (the IBKR tiles) is built when the first prices arrive: y 276 to 730

    ' row 3: movers, picks, coming up (lines of text)
    m.moversPanel = panel(80, 790, 400, 194, "Top movers", "close  ·  O/N")
    m.chartPanel = panel(504, 790, 336, 194, "Futures 24 h", "")
    m.chart = { g: invalid, n: -1 }
    m.picksPanel = panel(864, 790, 440, 194, "Picks", "")
    m.nextPanel = panel(1328, 790, 512, 194, "Coming up", "")
    m.rows = { movers: [], picks: [], next: [] }

    ' footer
    ' market weather: the whole width, wrapping onto a second line if it is long
    m.weather = lbl(p, "", 80, 988, 1760, 22, false, m.C.white)
    m.weather.wrap = true
    m.weather.maxLines = 2
    m.message = lbl(p, "", 80, 1050, 1760, 20, true, m.C.amber)
end sub

' ------------------------------------------------------------ the IBKR tiles (like the IBKR board)
sub buildTiles(list as object)
    if m.tileGrid <> invalid then m.page.removeChild(m.tileGrid)
    m.tiles = []
    m.tileGrid = m.page.createChild("Group")
    m.tileGrid.translation = [80, 248]
    ' stocks first, then futures, each group starting on its own row
    ns = 0
    for each t in list
        if t.fut <> true then ns = ns + 1
    end for
    nf = list.count() - ns
    cols = 8
    for each c in [6, 7, 8]
        if rowsFor(ns, c) + rowsFor(nf, c) <= 3
            cols = c
            exit for
        end if
    end for
    gap = 14
    w = Int((1760 - (cols - 1) * gap) / cols)
    h = 150
    small = (w < 260)
    y = 0
    i = 0
    for each grp in [{ name: "STOCKS", n: ns, first: 0 }, { name: "FUTURES", n: nf, first: ns }]
        if grp.n > 0 and y < 500
            lbl(m.tileGrid, grp.name, 0, y, 400, 18, true, m.C.cyan)
            y = y + 24
            for k = 0 to grp.n - 1
                if k > 0 and k mod cols = 0 then y = y + h + 8
                if y + h > 532 then exit for
                makeTile(grp.first + k, (k mod cols) * (w + gap), y, w, h, small)
                i = i + 1
            end for
            y = y + h + 8
        end if
    end for
    m.shown = i
end sub

function rowsFor(n as integer, cols as integer) as integer
    if n = 0 then return 0
    return Int((n + cols - 1) / cols)
end function

sub makeTile(idx as integer, x as integer, y as integer, w as integer, h as integer, small as boolean)
    g = m.tileGrid.createChild("Group")
    g.translation = [x, y]
    rect(g, 0, 0, w, h, m.C.line)
    fill = rect(g, 1, 1, w - 2, h - 2, m.C.panel)
    bar = rect(g, 1, 1, 4, h - 2, m.C.panel)
    flash = rect(g, 1, 1, w - 2, h - 2, m.C.flashUp)
    flash.visible = false
    sym = lbl(g, "", 16, 5, w - 32, 22, true, m.C.sym)
    tag = lbl(g, "", w - 76, 11, 60, 16, false, m.C.cyan, "right")
    tag.visible = false
    price = lbl(g, "--", 16, 1, w - 32, 28, true, m.C.white, "right")
    rect(g, 14, 34, w - 28, 1, m.C.line)
    ' the IBKR board's change boxes: 1M 5M 15M 45M 4H 8H on the first row, 1D 3D 1W 1MO O/N on the second
    boxes = []
    pad = 16
    if small then pad = 10
    for k = 0 to 10
        if k < 6
            n = 6
            bx0 = k
            by = 38
        else
            n = 5
            bx0 = k - 6
            by = 71
        end if
        bw = Int((w - 2 * pad - (n - 1) * 2) / n)
        bx = pad + bx0 * (bw + 2)
        rect(g, bx, by, bw, 31, m.C.box)
        name = lbl(g, "", bx, by + 1, bw, 11, false, m.C.cyan, "center")
        vsize = 15
        if bw < 40 then vsize = 13
        val = lbl(g, "--", bx, by + 14, bw, vsize, true, m.C.dim, "center")
        boxes.push({ name: name, val: val, narrow: (bw < 40) })
    end for
    ' O/N, 1W and 52W sliders: low, a track with a dot at today's price, high
    sliders = []
    nw = 54
    if small then nw = 0
    tx = 16 + 36 + nw + 4
    tw = w - 16 - tx - nw - 4
    if nw > 0 then tw = tw - 4
    for k = 0 to 2
        sy = 107 + k * 13
        lbl(g, ["O/N", "1W", "52W"][k], 16, sy - 1, 36, 12, true, m.C.dim)
        lo = lbl(g, "", 52, sy - 1, nw, 12, false, m.C.dim)
        rect(g, tx, sy + 5, tw, 3, m.C.line)
        dot = rect(g, tx, sy + 2, 8, 9, m.C.white)
        hi = lbl(g, "", tx + tw + 4, sy - 1, nw, 12, false, m.C.dim, "right")
        lo.visible = (nw > 0)
        hi.visible = (nw > 0)
        sliders.push({ lo: lo, hi: hi, dot: dot, x0: tx, tw: tw })
    end for
    m.tiles.push({ fill: fill, bar: bar, flash: flash, sym: sym, tag: tag, price: price, boxes: boxes, sliders: sliders, raw: invalid, idx: idx })
end sub

sub onTiles()
    d = m.feed.tiles
    if d = invalid or d.tiles = invalid then return
    list = d.tiles
    nf = 0
    for each t in list
        if t.fut = true then nf = nf + 1
    end for
    shape = list.count().ToStr() + "/" + nf.ToStr()
    if m.shape <> shape
        buildTiles(list)
        m.shape = shape
    end if
    flashed = false
    for each e in m.tiles
        t = list[e.idx]
        e.sym.text = txt(t.sym)
        e.price.text = txt(t.last)
        e.price.color = dirColor(t.dir)
        if t.zone > 0
            e.fill.color = m.C.upbg
            e.bar.color = m.C.green
        else if t.zone < 0
            e.fill.color = m.C.downbg
            e.bar.color = m.C.red
        else
            e.fill.color = m.C.panel
            e.bar.color = m.C.panel
        end if
        ' flash the tile when the price moves, green up, red down (like the IBKR board)
        if e.raw <> invalid and t.raw <> invalid and t.raw <> e.raw and t.raw > 0
            if t.raw > e.raw then e.flash.color = m.C.flashUp else e.flash.color = m.C.flashDown
            e.flash.visible = true
            flashed = true
        end if
        e.raw = t.raw
        if t.bars <> invalid
            for k = 0 to 2
                if k < t.bars.count()
                    b = t.bars[k]
                    sl = e.sliders[k]
                    sl.lo.text = txt(b.lo)
                    sl.hi.text = txt(b.hi)
                    sl.dot.visible = (b.ok = true)
                    if b.ok <> true
                        sl.lo.text = ""
                        sl.hi.text = ""
                    end if
                    sl.dot.translation = [sl.x0 + Int(b.pos * (sl.tw - 8)), sl.dot.translation[1]]
                    if b.edge > 0
                        sl.dot.color = m.C.green
                    else if b.edge < 0
                        sl.dot.color = m.C.red
                    else
                        sl.dot.color = m.C.white
                    end if
                end if
            end for
        end if
        if t.chg <> invalid
            for k = 0 to 10
                if k < t.chg.count()
                    c = t.chg[k]
                    e.boxes[k].name.text = txt(c.name)
                    if e.boxes[k].narrow and c.short <> invalid then e.boxes[k].val.text = txt(c.short) else e.boxes[k].val.text = txt(c.val)
                    if c.dir > 0
                        e.boxes[k].val.color = m.C.green
                    else if c.dir < 0
                        e.boxes[k].val.color = m.C.red
                    else
                        e.boxes[k].val.color = m.C.dim
                    end if
                end if
            end for
        end if
    end for
    if flashed
        m.flashTimer.control = "stop"
        m.flashTimer.control = "start"
    end if
    if list.count() = 0
        showMessage("The IBKR board has no symbols yet")
    end if
end sub

sub endFlash()
    for each e in m.tiles
        e.flash.visible = false
    end for
end sub

' ------------------------------------------------------------ the remote
' OK = reconnect now. The TV only asks the board again; it can't change anything on the board.
function onKeyEvent(key as string, press as boolean) as boolean
    if press and chKey(key) then return true
    if press and (key = "OK" or key = "play")
        showMessage("Reconnecting to the board...")
        m.feed.retry = true
        return true
    end if
    return false
end function

' ------------------------------------------------------------ filling in the rest
sub onProblem()
    t = m.feed.problem
    if t <> "" then showMessage(t + "   (still showing the last numbers it got; press OK on the remote to try again)") else showMessage("")
end sub

sub onResult()
    d = m.feed.result
    if d = invalid then return
    m.clock.text = txt(d.clock)
    m.session.text = txt(d.session) + "  ·  " + txt(d.session_note)

    ms = d.meters
    if ms = invalid then ms = []
    for i = 0 to 2
        k = m.meters[i]
        if i < ms.count()
            x = ms[i]
            k.title.text = UCase(txt(x.title))
            if i = 0 then k.note.text = "World tab" else k.note.text = txt(d.meters_note)
            k.score.text = txt(x.score)
            k.score.color = dirColor(x.dir)
            k.word.text = txt(x.label)
            k.word.color = dirColor(x.dir)
            v = x.value
            if v = invalid then v = 0
            k.needle.translation = [28 + Int((v + 100) / 200.0 * 498), 84]
            k.needle.visible = (x.ok = true)
        end if
    end for

    ' top movers
    clearRows(m.rows.movers)
    g = m.moversPanel.g
    mv = d.movers
    if mv = invalid then mv = []
    y = 50
    for each r in mv
        m.rows.movers.push(lbl(g, txt(r.sym), 22, y, 100, 24, true, m.C.white))
        m.rows.movers.push(lbl(g, txt(r.last), 96, y + 2, 104, 22, false, m.C.white, "right"))
        m.rows.movers.push(lbl(g, txt(r.chg), 204, y, 100, 22, true, dirColor(r.dir), "right"))
        m.rows.movers.push(lbl(g, txt(r.on), 306, y + 2, 78, 20, false, dirColor(r.ondir), "right"))
        y = y + 34
    end for
    if mv.count() = 0 then m.rows.movers.push(lbl(g, "No board prices yet", 28, 54, 350, 24, false, m.C.dim))

    drawChart(d.fchart)

    ' picks, two columns of 4
    clearRows(m.rows.picks)
    g = m.picksPanel.g
    m.picksPanel.note.text = txt(d.picks_note)
    pk = d.picks
    if pk = invalid then pk = []
    for i = 0 to pk.count() - 1
        r = pk[i]
        x = 28 + Int(i / 4) * 200
        y = 50 + (i mod 4) * 34
        m.rows.picks.push(lbl(g, txt(r.sym), x, y, 90, 26, true, m.C.white))
        m.rows.picks.push(lbl(g, txt(r.chg), x + 70, y, 110, 26, true, dirColor(r.dir), "right"))
    end for
    if pk.count() = 0 then m.rows.picks.push(lbl(g, "No picks yet", 28, 54, 380, 28, false, m.C.dim))

    ' coming up + latest alerts
    clearRows(m.rows.next)
    g = m.nextPanel.g
    y = 48
    ev = d.events
    if ev = invalid then ev = []
    for each e in ev
        m.rows.next.push(lbl(g, txt(e.when), 28, y, 456, 22, true, m.C.amber))
        m.rows.next.push(lbl(g, txt(e.title), 28, y + 26, 456, 24, false, m.C.white))
        y = y + 52
    end for
    if ev.count() = 0
        m.rows.next.push(lbl(g, "No big reports due", 28, y, 456, 24, false, m.C.dim))
        y = y + 40
    end if
    al = d.alerts
    if al = invalid then al = []
    for each a in al
        if y > 150 then exit for
        m.rows.next.push(lbl(g, txt(a.when) + "  " + txt(a.text), 28, y, 456, 22, false, m.C.white))
        y = y + 28
    end for

    m.weather.text = txt(d.weather)
    m.status.text = "·   Updated " + txt(d.updated) + " via " + m.feed.route + "  ·  " + txt(d.status)
    if d.status_ok = true then m.status.color = m.C.dim else m.status.color = m.C.red
end sub

' ------------------------------------------------------------ the futures chart (last 24 hours)
' One line per IBKR future, % change since 24 hours ago, drawn as short bars from point to point.
sub drawChart(c as dynamic)
    if c = invalid or c.lines = invalid then return
    lines = c.lines
    pg = m.chartPanel.g
    px0 = 18
    pw = 196
    py0 = 56
    ph = 124
    steps = 72
    if c.steps <> invalid then steps = c.steps
    if m.chart.n <> lines.count()
        if m.chart.g <> invalid then pg.removeChild(m.chart.g)
        g = pg.createChild("Group")
        m.chart = { g: g, n: lines.count(), zero: rect(g, px0, py0, pw, 1, m.C.line), segs: [], keys: [] }
        m.chart.top = lbl(g, "", px0, py0 - 2, pw, 12, false, m.C.dim)
        m.chart.bottom = lbl(g, "", px0, py0 + ph - 10, pw, 12, false, m.C.dim)
        m.chart.since = lbl(g, "", px0, py0 + ph - 10, pw, 12, false, m.C.dim, "right")
        for i = 0 to lines.count() - 1
            segs = []
            for k = 0 to steps - 1
                segs.push(rect(g, px0, py0, 2, 2, m.C.white))
            end for
            m.chart.segs.push(segs)
            m.chart.keys.push({ sw: rect(g, 0, 0, 10, 4, m.C.white), t: lbl(g, "", 0, 0, 98, 17, true, m.C.white) })
        end for
        if lines.count() = 0 then m.chart.keys.push({ t: lbl(g, "Waiting for futures prices", 18, 70, 300, 20, false, m.C.dim) })
    end if
    if lines.count() = 0 then return
    palette = ["0x5CC8DBFF", "0xF2B84BFF", "0xD07AF2FF", "0xF2F2F2FF", "0xA8E05CFF", "0xFF8A3DFF", "0x5C8CFFFF", "0xFF7AB8FF"]
    if c.zero <> invalid then m.chart.zero.translation = [px0, py0 + Int((1 - c.zero) * ph)]
    m.chart.top.text = txt(c.top)
    m.chart.bottom.text = txt(c.bottom)
    m.chart.since.text = "since " + txt(c.from)
    rowH = 16
    if lines.count() <= 5 then rowH = 24
    for i = 0 to lines.count() - 1
        ln = lines[i]
        col = palette[ln.c mod 8]
        pts = ln.pts
        segs = m.chart.segs[i]
        for k = 0 to segs.count() - 1
            sg = segs[k]
            if k + 1 < pts.count() and pts[k] >= 0 and pts[k + 1] >= 0
                xa = px0 + Int(k * pw / steps)
                xb = px0 + Int((k + 1) * pw / steps)
                ya = py0 + Int((1 - pts[k]) * ph)
                yb = py0 + Int((1 - pts[k + 1]) * ph)
                top = ya
                if yb < top then top = yb
                sg.translation = [xa, top - 1]
                sg.width = xb - xa + 1
                sg.height = Abs(ya - yb) + 2
                sg.color = col
                sg.visible = true
            else
                sg.visible = false
            end if
        end for
        ky = py0 - 6 + i * rowH
        k = m.chart.keys[i]
        k.sw.translation = [220, ky + 7]
        k.sw.color = col
        k.t.translation = [232, ky]
        k.t.text = txt(ln.sym) + " " + txt(ln.val)
        k.t.color = col
    end for
end sub
