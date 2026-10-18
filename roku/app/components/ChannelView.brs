' Alex's channel on the TV (jb-channel): the board as a news channel. The Mac writes the rundown and
' the anchor's voice (gateway/channel.py); this only plays it. Right arrow = channel, left arrow = board.
' In the channel: OK or Play = sound on/off, right arrow = next segment, Back = board.

sub chInit()
    m.chPal = { cyan: "0x5CC8DBFF", amber: "0xF2B84BFF", red: "0xFF5C52FF", purple: "0xA78BFAFF" }
    m.ch = m.top.createChild("Group")
    m.ch.visible = false
    rect(m.ch, 0, 0, 1920, 1080, m.C.bg)
    ' header
    rect(m.ch, 60, 40, 96, 64, m.C.cyan)
    lbl(m.ch, "AH", 60, 52, 96, 36, true, m.C.bg, "center")
    lbl(m.ch, "ALEX'S CHANNEL", 172, 40, 700, 36, true, m.C.white)
    m.chDot = rect(m.ch, 174, 90, 14, 14, m.C.red)
    m.chLive = lbl(m.ch, "LIVE  ·  read only", 198, 84, 430, 22, true, m.C.red)
    ' QUIET HOURS (1-3 am) / WEEKEND DESK: a big purple band in the header for the whole show (jb-anchor)
    m.chQ = m.ch.createChild("Group")
    m.chQ.translation = [640, 36]
    rect(m.chQ, 0, 0, 760, 92, m.chPal.purple)
    m.chQ1 = lbl(m.chQ, "", 28, 6, 620, 44, true, "0x160C33FF")
    m.chQ2 = lbl(m.chQ, "", 30, 60, 700, 20, true, "0x160C33FF")
    m.chQMoon = rect(m.chQ, 690, 24, 40, 40, "0x160C33FF")
    rect(m.chQ, 702, 18, 40, 40, m.chPal.purple)
    m.chQ.visible = false
    rect(m.ch, 1440, 36, 420, 92, m.C.panel)
    rect(m.ch, 1440, 36, 6, 92, m.C.cyan)
    m.chK = [lbl(m.ch, "", 1462, 46, 130, 20, true, m.C.dim), lbl(m.ch, "", 1600, 46, 130, 20, true, m.C.dim)]
    m.chV = [lbl(m.ch, "", 1462, 72, 140, 32, true, m.C.white), lbl(m.ch, "", 1600, 72, 140, 32, true, m.C.white)]
    lbl(m.ch, "ET", 1700, 46, 146, 20, true, m.C.dim, "right")
    m.chClock = lbl(m.ch, "", 1680, 72, 166, 32, true, m.C.white, "right")
    ' segment parts
    m.chBody = m.ch.createChild("Group")
    m.chSlab = m.ch.createChild("Group")
    m.chSlabBg = rect(m.chSlab, 0, 0, 700, 70, m.C.cyan)
    m.chSlabT = lbl(m.chSlab, "", 30, 10, 1200, 44, true, m.C.bg)
    m.chSub = lbl(m.ch, "", 60, 234, 1700, 24, false, m.C.dim)
    m.chBanner = m.ch.createChild("Group")
    m.chBanner.translation = [60, 828]
    rect(m.chBanner, 0, 0, 1800, 64, m.C.red)
    rect(m.chBanner, 0, 0, 240, 64, m.C.white)
    lbl(m.chBanner, "BREAKING", 0, 12, 240, 32, true, m.C.red, "center")
    m.chBannerT = lbl(m.chBanner, "", 270, 14, 1500, 30, true, m.C.white)
    m.chBanner.visible = false
    ' closed captions (sound off): the anchor's words, two lines at a time
    m.chCC = m.ch.createChild("Group")
    m.chCCBg = rect(m.chCC, 0, 0, 1400, 96, "0x000000D0")
    m.chCCT = lbl(m.chCC, "", 24, 10, 1352, 32, true, m.C.white, "center")
    m.chCCT.wrap = true
    m.chCCT.maxLines = 2
    m.chCC.visible = false
    m.chCCList = []
    m.chCCShown = -1
    m.chNext = lbl(m.ch, "", 60, 912, 1300, 22, true, m.C.dim)
    m.chAir = lbl(m.ch, "", 1300, 912, 450, 22, true, m.C.amber, "right")
    m.chEq = []
    for i = 0 to 8
        m.chEq.push(rect(m.ch, 1770 + i * 10, 938, 6, 4, m.C.amber))
    end for
    ' the crawl
    rect(m.ch, 0, 960, 1920, 120, "0x0A2442FF")
    m.chCrawl = m.ch.createChild("Group")
    m.chCrawl.translation = [200, 0]
    m.chCrawlW = 0
    m.chCrawlX = 0
    rect(m.ch, 0, 960, 200, 120, m.C.cyan)
    lbl(m.ch, "TICKER", 0, 1000, 200, 30, true, m.C.bg, "center")
    m.chMsg = lbl(m.ch, "", 60, 1040, 1800, 20, true, m.C.amber)

    m.chAudio = m.top.createChild("Audio")
    m.chAudio.observeField("state", "chAudioState")
    m.chTick = m.top.createChild("Timer")
    m.chTick.duration = 0.04
    m.chTick.repeat = true
    m.chTick.observeField("fire", "chFrame")
    m.chSeg = m.top.createChild("Timer")
    m.chSeg.observeField("fire", "chNext")
    m.chGap = m.top.createChild("Timer")
    m.chGap.duration = 0.9
    m.chGap.observeField("fire", "chNext")
    m.chWait = m.top.createChild("Timer")
    m.chWait.duration = 5
    m.chWait.observeField("fire", "chStartFirst")
    m.chRetryT = m.top.createChild("Timer")
    m.chRetryT.duration = 5
    m.chRetryT.observeField("fire", "chRetry")

    m.chTask = CreateObject("roSGNode", "ChannelTask")
    m.chTask.observeField("rundown", "chOnRundown")
    m.chTask.observeField("clip", "chOnClip")
    m.chTask.observeField("problem", "chOnProblem")
    m.chTask.control = "RUN"
    m.chR = invalid
    m.chIdx = -1
    m.chClips = {}
    m.chMuted = false
    m.chOn = false
    m.chPlaying = false
    m.chWaitingFirst = false
    m.chAnims = []
    m.chDraw = ""
end sub

' ------------------------------------------------------------ switching
sub chShow(on as boolean)
    m.top.setFocus(true)
    m.chOn = on
    m.ch.visible = on
    m.page.visible = not on
    if on
        m.chTick.control = "start"
        m.chIdx = -1
        m.chR = invalid
        m.chMsg.text = "Tuning in..."
        m.chTask.want = "rundown"
    else
        m.chTick.control = "stop"
        m.chSeg.control = "stop"
        m.chGap.control = "stop"
        m.chWait.control = "stop"
        m.chRetryT.control = "stop"
        m.chAudio.control = "stop"
        m.chPlaying = false
    end if
end sub

function chKey(key as string) as boolean
    if not m.chOn
        if key = "right"
            chShow(true)
            return true
        end if
        return false
    end if
    if key = "left" or key = "back"
        chShow(false)
        return true
    end if
    if key = "right" or key = "fastforward"
        m.chAudio.control = "stop"
        chNext()
        return true
    end if
    if key = "OK" or key = "play"
        m.chMuted = not m.chMuted
        if m.chMuted
            m.chAudio.control = "stop"
            m.chPlaying = false
            m.chAir.text = "SOUND OFF · CAPTIONS ON  (OK = sound)"
        else
            m.chAir.text = "SOUND ON"
        end if
        return true
    end if
    return false
end function

' ------------------------------------------------------------ the rundown and the sound files
sub chOnProblem()
    if m.chOn and m.chTask.problem <> ""
        m.chMsg.text = m.chTask.problem
        if m.chR = invalid then m.chRetryT.control = "start"
    end if
end sub

sub chOnRundown()
    r = m.chTask.rundown
    if r = invalid or r.segments = invalid then return
    m.chR = r
    m.chMsg.text = ""
    chBuildCrawl(r.ticker)
    ' fetch every sound file now, in order, so each is ready before its turn
    for each s in r.segments
        if s.audio <> invalid and s.audio <> "" and m.chClips[s.audio] = invalid
            m.chTask.want = "clip:" + s.audio
        end if
    end for
    m.chIdx = -1
    first = r.segments[0]
    if first <> invalid and first.audio <> invalid and first.audio <> "" and m.chClips[first.audio] = invalid and not m.chMuted
        m.chWaitingFirst = true
        m.chWait.control = "start"
    else
        chNext()
    end if
end sub

sub chOnClip()
    c = m.chTask.clip
    if c = invalid then return
    if c.ok = true then m.chClips[c.name] = c.path
    if m.chWaitingFirst and m.chR <> invalid and m.chR.segments[0].audio = c.name then chStartFirst()
end sub

sub chStartFirst()
    if not m.chWaitingFirst then return
    m.chWaitingFirst = false
    m.chWait.control = "stop"
    chNext()
end sub

' when the rundown is waiting to be made, ask again shortly
sub chRetry()
    if m.chOn then m.chTask.want = "rundown"
end sub

' ------------------------------------------------------------ playing segments
sub chNext()
    m.chSeg.control = "stop"
    m.chGap.control = "stop"
    if not m.chOn then return
    if m.chR = invalid
        m.chTask.want = "rundown"
        return
    end if
    m.chIdx = m.chIdx + 1
    if m.chIdx >= m.chR.segments.count()
        m.chR = invalid
        m.chTask.want = "rundown"
        return
    end if
    chPlay(m.chR.segments[m.chIdx])
end sub

sub chAudioState()
    st = m.chAudio.state
    if st = "playing"
        m.chPlaying = true
    else if st = "finished"
        m.chPlaying = false
        if m.chOn
            m.chSeg.control = "stop"
            m.chGap.control = "start"
        end if
    else if st = "error" or st = "stopped"
        m.chPlaying = false
    end if
end sub

sub chPlay(s as object)
    m.chT0 = CreateObject("roTimespan")
    dur = s.dur
    if dur = invalid then dur = 8
    m.chDur = dur
    ' the frame: title slab slides in, sub line, banner, up next
    m.chSlabT.text = txt(s.title)
    w = Len(txt(s.title)) * 30 + 70
    if w < 260 then w = 260
    m.chSlabBg.width = w
    col = m.chPal[txt(s.color)]
    if col = invalid then col = m.chPal.cyan
    m.chSlabBg.color = col
    m.chSub.text = txt(s.sub)
    show = txt(s.show)
    if show = "" then show = "ALEX'S CHANNEL"
    m.chLive.text = "LIVE  ·  " + show + "  ·  read only"
    bn = txt(m.chR.banner)
    m.chQ.visible = (bn <> "")
    if bn <> ""
        parts = bn.Split("  ·  ")
        m.chQ1.text = parts[0]
        rest = []
        for k = 1 to parts.count() - 1
            rest.push(parts[k])
        end for
        m.chQ2.text = rest.Join("  ·  ")
    end if
    m.chBanner.visible = (s.kind = "breaking")
    if s.kind = "breaking" then m.chBannerT.text = txt(s.line)
    up = []
    for k = m.chIdx + 1 to m.chIdx + 3
        if k < m.chR.segments.count()
            t = txt(m.chR.segments[k].title)
            up.push(Left(t, 1) + LCase(Mid(t, 2)))
        end if
    end for
    if up.count() > 0 then m.chNext.text = "UP NEXT:  " + up.Join("  ·  ") else m.chNext.text = "UP NEXT:  a fresh rundown"
    ' the stage
    m.ch.removeChild(m.chBody)
    m.chBody = CreateObject("roSGNode", "Group")
    m.ch.insertChild(m.chBody, 1)
    m.chAnims = []
    m.chDraw = txt(s.kind)
    m.chS = s
    cctext = s.cc
    if cctext = invalid then cctext = s.say
    m.chCCList = ccChunks(txt(cctext))
    m.chCCShown = -1
    if s.kind = "breaking" then m.chCC.translation = [260, 732] else m.chCC.translation = [260, 812]
    chAnim(m.chSlab, 60, 150, 0, "slab")
    m.chSub.opacity = 0
    chAnim(m.chSub, 60, 234, 0.3, "fade")
    if s.kind = "open" then chOpen(s)
    if s.kind = "futures" then chFutures(s)
    if s.kind = "movers" then chMovers(s)
    if s.kind = "picks" then chPicks(s)
    if s.kind = "news" then chNews(s)
    if s.kind = "sectors" then chSectors(s)
    if s.kind = "coming" then chComing(s)
    if s.kind = "weather" then chWeather(s)
    if s.kind = "breaking" then chBreaking(s)
    if s.kind = "dive" or s.kind = "desk" or s.kind = "special" or s.kind = "explainer" then chDive(s)
    ' the anchor
    path = invalid
    if s.audio <> invalid and s.audio <> "" then path = m.chClips[s.audio]
    if path <> invalid and not m.chMuted
        cn = CreateObject("roSGNode", "ContentNode")
        cn.url = path
        m.chAudio.content = cn
        m.chAudio.control = "play"
        m.chAir.text = "ON AIR: " + LCase(txt(s.title))
        m.chSeg.duration = dur + 6
    else
        m.chAudio.control = "stop"
        if m.chMuted then m.chAir.text = "SOUND OFF · CAPTIONS ON  (OK = sound)" else m.chAir.text = ""
        m.chSeg.duration = dur
    end if
    m.chSeg.control = "start"
end sub

' ------------------------------------------------------------ animation (one timer, 25 times a second)
sub chAnim(node as object, x as integer, y as integer, at as float, kind as string)
    node.translation = [x, y]
    if kind <> "slab" then node.opacity = 0
    m.chAnims.push({ n: node, x: x, y: y, at: at, k: kind })
end sub

function ease(t as float) as float
    if t <= 0 then return 0
    if t >= 1 then return 1
    u = 1 - t
    return 1 - u * u * u
end function

sub chFrame()
    if not m.chOn then return
    ' the crawl
    if m.chCrawlW > 0
        m.chCrawlX = m.chCrawlX - 4
        if -m.chCrawlX > m.chCrawlW then m.chCrawlX = m.chCrawlX + m.chCrawlW
        m.chCrawl.translation = [200 + m.chCrawlX, 960]
    end if
    ' the corner: live from the board's tiles (every 2 s) and clock
    chCorner()
    for each b in m.chEq
        if m.chPlaying then h = 4 + Rnd(28) else h = 4
        b.height = h
        b.translation = [b.translation[0], 942 - h]
    end for
    if m.chT0 = invalid then return
    el = m.chT0.TotalMilliseconds() / 1000.0
    for each a in m.chAnims
        t = ease((el - a.at) / 0.5)
        if a.k = "slab"
            a.n.translation = [a.x - Int((1 - t) * 900), a.y]
        else if a.k = "fade"
            a.n.opacity = t
        else if a.k = "pop"
            a.n.opacity = t
            a.n.translation = [a.x, a.y + Int((1 - t) * 30)]
        else if a.k = "right"
            a.n.opacity = t
            a.n.translation = [a.x + Int((1 - t) * 140), a.y]
        else if a.k = "flip"
            a.n.opacity = t
            a.n.scale = [0.05 + 0.95 * t, 1]
        end if
    end for
    p = el / m.chDur
    if p > 1 then p = 1
    chCaptions(p)
    if m.chDraw = "futures" then chFuturesFrame(p)
    if m.chDraw = "movers" then chMoversFrame(p)
    if m.chDraw = "weather" then chWeatherFrame(p)
    if m.chDraw = "breaking" then m.chBorder.visible = (Int(p * 20) mod 2 = 0)
    if m.chDraw = "dive" or m.chDraw = "desk" or m.chDraw = "special" or m.chDraw = "explainer" then chDiveFrame(p)
    if m.chQ.visible then m.chQMoon.opacity = 0.6 + 0.4 * Abs(((el * 1000) mod 3000) / 1500.0 - 1)
end sub

sub chCorner()
    d = m.feed.tiles
    shown = 0
    if d <> invalid and d.tiles <> invalid
        for each want in ["ES", "NQ"]
            for each t in d.tiles
                if t.fut = true and t.sym = want and shown < 2
                    v = "--"
                    dr = 0
                    for each c in t.chg
                        if c.name = "1D"
                            v = txt(c.val) + "%"
                            dr = c.dir
                        end if
                    end for
                    m.chK[shown].text = want + "  (1D)"
                    m.chV[shown].text = v
                    m.chV[shown].color = dirColor(dr)
                    shown = shown + 1
                end if
            end for
        end for
    end if
    r = m.feed.result
    if r <> invalid and r.clock <> invalid then m.chClock.text = txt(r.clock).Replace(" ET", "").Replace(" AM", "").Replace(" PM", "")
end sub

sub chBuildCrawl(items as dynamic)
    m.chCrawl.removeChildrenIndex(m.chCrawl.getChildCount(), 0)
    if items = invalid then return
    x = 30
    for pass = 0 to 1
        for each it in items
            l = lbl(m.chCrawl, txt(it.t), x, 34, 0, 34, true, dirColor(it.dir))
            l.width = 0
            w = l.boundingRect().width
            if w <= 0 then w = Len(txt(it.t)) * 20
            x = x + w + 56
        end for
        if pass = 0 then m.chCrawlW = x - 30
    end for
    m.chCrawlX = 0
end sub

' ------------------------------------------------------------ one drawing per kind of segment
function cpanel(x as integer, y as integer, w as integer, h as integer, edge as string, edgeTop as boolean) as object
    g = m.chBody.createChild("Group")
    rect(g, 0, 0, w, h, m.C.panel)
    if edge <> ""
        if edgeTop then rect(g, 0, 0, w, 8, edge) else rect(g, 0, 0, 8, h, edge)
    end if
    g.translation = [x, y]
    return g
end function

sub chOpen(s as object)
    first = txt(s.headline)
    if first = ""
        first = txt(s.say)
        i = Instr(1, first, ".")
        if i > 0 then first = Left(first, i)
    end if
    g = m.chBody.createChild("Group")
    lbl(g, first, 0, 0, 1800, 80, true, m.C.white)
    chAnim(g, 60, 300, 0.2, "pop")
    ms = s.meters
    if ms = invalid then ms = []
    for i = 0 to ms.count() - 1
        x = 60 + i * 610
        mt = ms[i]
        p = cpanel(x, 470, 580, 300, m.C.cyan, true)
        lbl(p, UCase(txt(mt.title)), 30, 28, 520, 26, true, m.C.cyan)
        lbl(p, txt(mt.score), 30, 70, 520, 90, true, dirColor(mt.dir))
        lbl(p, txt(mt.label), 30, 190, 520, 30, true, dirColor(mt.dir))
        for st = 0 to 25
            t = st / 25.0
            if t < 0.5 then c = mix("FF5C52", "F2B84B", t * 2) else c = mix("F2B84B", "3EE07A", (t - 0.5) * 2)
            rect(p, 30 + st * 20, 250, 20, 12, c)
        end for
        v = mt.value
        if v = invalid then v = 0
        rect(p, 30 + Int((v + 100) / 200.0 * 512), 240, 8, 32, m.C.white)
        chAnim(p, x, 470, 0.5 + i * 0.4, "pop")
    end for
end sub

sub chFutures(s as object)
    p = cpanel(60, 290, 1240, 520, "", false)
    for k = 1 to 4
        rect(p, 0, k * 104, 1240, 1, m.C.line)
    end for
    pts = s.series
    if pts = invalid then pts = []
    if s.dir < 0
        col = m.C.red
        fill = "0xFF5C5224"
    else
        col = m.C.green
        fill = "0x3EE07A24"
    end if
    m.chSegs = []
    n = pts.count()
    for k = 0 to n - 2
        if pts[k] >= 0 and pts[k + 1] >= 0
            xa = 20 + Int(k * 1200 / (n - 1))
            xb = 20 + Int((k + 1) * 1200 / (n - 1))
            ya = 500 - Int(pts[k] * 480)
            yb = 500 - Int(pts[k + 1] * 480)
            top = ya
            if yb < top then top = yb
            f = rect(p, xa, ya, xb - xa, 520 - ya, fill)
            ln = rect(p, xa, top - 2, xb - xa + 2, Abs(ya - yb) + 5, col)
            f.visible = false
            ln.visible = false
            m.chSegs.push({ f: f, l: ln, x: xb, y: yb })
        end if
    end for
    m.chHead = rect(p, 0, 0, 14, 14, m.C.white)
    m.chHead.visible = false
    labs = s.labels
    if labs = invalid then labs = []
    for k = 0 to labs.count() - 1
        x = 80 + Int(k * 1180 / 4)
        if k = labs.count() - 1 then x = x - 40
        lbl(m.chBody, txt(labs[k]), x, 818, 200, 20, false, m.C.dim)
    end for
    g = m.chBody.createChild("Group")
    lbl(g, txt(s.name), 0, 0, 520, 26, true, m.C.cyan)
    chAnim(g, 1340, 300, 0.1, "fade")
    m.chBig = lbl(m.chBody, "", 1340, 330, 560, 110, true, dirColor(s.dir))
    g = m.chBody.createChild("Group")
    lbl(g, txt(s.price), 0, 0, 520, 52, true, m.C.white)
    chAnim(g, 1340, 470, 0.6, "pop")
    os = s.others
    if os = invalid then os = []
    for i = 0 to os.count() - 1
        o = os[i]
        r = cpanel(1340, 560 + i * 52, 520, 44, "", false)
        lbl(r, txt(o.sym), 20, 7, 240, 26, true, m.C.white)
        lbl(r, txt(o.val), 260, 7, 240, 26, true, dirColor(o.dir), "right")
        chAnim(r, 1340, 560 + i * 52, 0.9 + i * 0.25, "right")
    end for
end sub

sub chFuturesFrame(p as float)
    k = ease(p / 0.6)
    n = m.chSegs.count()
    upto = Int(k * n)
    for i = 0 to n - 1
        vis = (i < upto)
        m.chSegs[i].f.visible = vis
        m.chSegs[i].l.visible = vis
    end for
    if upto > 0
        h = m.chSegs[upto - 1]
        m.chHead.translation = [h.x - 6, 500 - (500 - h.y) - 6]
        m.chHead.visible = true
    end if
    s = m.chS
    if s.bigv <> invalid
        v = s.bigv * k
        sign = "+"
        if v < 0 then sign = "-"
        m.chBig.text = sign + fixed2(Abs(v)) + "%"
    else
        m.chBig.text = txt(s.big)
    end if
end sub

function fixed2(v as float) as string
    c = Int(v * 100 + 0.5)
    w = Int(c / 100)
    f = c - w * 100
    fs = f.ToStr()
    if f < 10 then fs = "0" + fs
    return w.ToStr() + "." + fs
end function

sub chMovers(s as object)
    rows = s.rows
    if rows = invalid then rows = []
    m.chBars = []
    for i = 0 to rows.count() - 1
        r = rows[i]
        y = 300 + i * 84
        g = cpanel(60, y, 1800, 72, "", false)
        lbl(g, txt(r.sym), 30, 12, 200, 40, true, m.C.white)
        lbl(g, txt(r.last), 220, 16, 200, 34, false, m.C.white, "right")
        if r.dir < 0 then c = m.C.red else c = m.C.green
        b = rect(g, 460, 22, 1, 28, c)
        m.chBars.push({ b: b, v: r.bar, i: i })
        lbl(g, txt(r.val), 1400, 12, 200, 40, true, dirColor(r.dir), "right")
        lbl(g, txt(r.on), 1610, 18, 170, 26, false, m.C.dim, "right")
        chAnim(g, 60, y, 0.2 + i * 0.22, "right")
    end for
end sub

sub chMoversFrame(p as float)
    for each b in m.chBars
        v = b.v
        if v = invalid then v = 0
        w = Int(900 * v * ease((p - 0.08 - b.i * 0.04) / 0.4))
        if w < 1 then w = 1
        b.b.width = w
    end for
end sub

sub chPicks(s as object)
    cards = s.cards
    if cards = invalid then cards = []
    for i = 0 to cards.count() - 1
        c = cards[i]
        x = 60 + i * 610
        if c.dir < 0 then e = m.C.red else e = m.C.green
        g = cpanel(x, 290, 580, 520, e, true)
        lbl(g, txt(c.sym), 34, 30, 360, 72, true, m.C.white)
        lbl(g, txt(c.tag), 300, 52, 250, 22, true, m.C.dim, "right")
        lbl(g, txt(c.val), 34, 130, 360, 60, true, dirColor(c.dir))
        lbl(g, txt(c.score), 300, 154, 250, 22, false, m.C.dim, "right")
        rect(g, 34, 236, 512, 2, m.C.line)
        lbl(g, "WHY", 34, 256, 200, 20, true, m.C.cyan)
        y = 290
        if c.why <> invalid
            for each w in c.why
                l = lbl(g, "• " + txt(w), 34, y, 512, 27, false, m.C.white)
                l.wrap = true
                l.maxLines = 2
                y = y + 76
            end for
        end if
        chAnim(g, x, 290, 0.3 + i * 0.7, "flip")
    end for
    if cards.count() = 0
        g = m.chBody.createChild("Group")
        lbl(g, "No picks yet.", 0, 0, 1000, 48, false, m.C.dim)
        chAnim(g, 60, 400, 0.2, "pop")
    end if
end sub

sub chNews(s as object)
    items = s.items
    if items = invalid then items = []
    for i = 0 to items.count() - 1
        n = items[i]
        y = 290 + i * 130
        g = cpanel(60, y, 1800, 116, m.C.cyan, false)
        lbl(g, txt(n.title), 30, 14, 1740, 36, true, m.C.white)
        meta = []
        for each t in [n.syms, n.source, n.ago]
            if txt(t) <> "" then meta.push(txt(t))
        end for
        lbl(g, meta.Join("  ·  "), 30, 68, 1700, 24, false, m.C.dim)
        chAnim(g, 60, y, 0.3 + i * 0.9, "right")
    end for
    if items.count() = 0
        g = m.chBody.createChild("Group")
        lbl(g, "No new headlines.", 0, 0, 1000, 48, false, m.C.dim)
        chAnim(g, 60, 400, 0.2, "pop")
    end if
end sub

sub chSectors(s as object)
    tiles = s.tiles
    if tiles = invalid then tiles = []
    for i = 0 to tiles.count() - 1
        t = tiles[i]
        x = 60 + (i mod 4) * 455
        y = 290 + Int(i / 4) * 178
        h = t.heat
        if h = invalid then h = 0
        if h >= 0 then c = mix("071A30", "1F8A4C", h) else c = mix("071A30", "A8332C", -h)
        g = m.chBody.createChild("Group")
        rect(g, 0, 0, 435, 162, m.C.line)
        rect(g, 1, 1, 433, 160, c)
        lbl(g, txt(t.name), 22, 18, 330, 26, true, m.C.white)
        lbl(g, txt(t.sym), 300, 20, 112, 20, false, m.C.dim, "right")
        lbl(g, txt(t.val), 22, 72, 400, 56, true, m.C.white)
        chAnim(g, x, y, 0.2 + i * 0.16, "pop")
    end for
end sub

sub chComing(s as object)
    ev = s.events
    if ev = invalid then ev = []
    for i = 0 to ev.count() - 1
        e = ev[i]
        y = 290 + i * 150
        g = cpanel(60, y, 1000, 134, m.C.amber, false)
        lbl(g, txt(e.when), 30, 16, 900, 30, true, m.C.amber)
        lbl(g, txt(e.title), 30, 62, 940, 40, true, m.C.white)
        chAnim(g, 60, y, 0.3 + i * 0.6, "right")
    end for
    if ev.count() = 0
        g = m.chBody.createChild("Group")
        lbl(g, "No big reports due.", 0, 0, 1000, 44, false, m.C.dim)
        chAnim(g, 60, 320, 0.2, "pop")
    end if
    g = m.chBody.createChild("Group")
    lbl(g, "YOUR LATEST ALERTS", 0, 0, 700, 24, true, m.C.cyan)
    chAnim(g, 1110, 290, 0.9, "pop")
    al = s.alerts
    if al = invalid then al = []
    for i = 0 to al.count() - 1
        g = m.chBody.createChild("Group")
        l = lbl(g, txt(al[i].when) + "  " + txt(al[i].text), 0, 0, 750, 28, false, m.C.white)
        l.wrap = true
        l.maxLines = 3
        chAnim(g, 1110, 336 + i * 110, 1.2 + i * 0.5, "pop")
    end for
end sub

sub chWeather(s as object)
    m.chWords = txt(s.line).Split(" ")
    m.chQuote = lbl(m.chBody, "", 60, 320, 1800, 60, true, m.C.white)
    m.chQuote.wrap = true
    m.chQuote.maxLines = 6
end sub

sub chWeatherFrame(p as float)
    n = m.chWords.count()
    k = p / 0.7
    if k > 1 then k = 1
    upto = Int(n * k + 0.999)
    out = []
    for i = 0 to upto - 1
        if i < n then out.push(m.chWords[i])
    end for
    m.chQuote.text = out.Join(" ")
end sub

sub chBreaking(s as object)
    g = m.chBody.createChild("Group")
    m.chBorder = rect(g, 0, 0, 1800, 420, m.C.red)
    rect(g, 3, 3, 1794, 414, "0x2A1622FF")
    lbl(g, "JUST IN", 50, 40, 600, 30, true, m.C.red)
    l = lbl(g, txt(s.line), 50, 100, 1700, 64, true, m.C.white)
    l.wrap = true
    l.maxLines = 3
    chAnim(g, 60, 300, 0.15, "pop")
end sub

' ------------------------------------------------------------ closed captions (when the sound is off)
function ccChunks(text as string) as object
    out = []
    cur = ""
    for each w in text.Split(" ")
        if cur <> "" and Len(cur) + 1 + Len(w) > 84
            out.push(cur)
            cur = w
        else if cur = ""
            cur = w
        else
            cur = cur + " " + w
        end if
    end for
    if cur <> "" then out.push(cur)
    return out
end function

sub chCaptions(p as float)
    n = m.chCCList.count()
    if not m.chMuted or n = 0
        m.chCC.visible = false
        return
    end if
    talk = (m.chDur - 1.2) / m.chDur
    if talk < 0.3 then talk = 0.3
    q = p / talk
    if q > 0.999 then q = 0.999
    k = Int(q * n)
    if k >= n then k = n - 1
    if k <> m.chCCShown
        m.chCCT.text = m.chCCList[k]
        m.chCCShown = k
    end if
    m.chCC.visible = true
end sub

' ------------------------------------------------------------ deep dives, specials, explainers, the desk view (jb-anchor)
' one picture: a 24 h chart with level lines (when there is one), a big number, chips and short points
sub chDive(s as object)
    col = m.chPal[txt(s.color)]
    if col = invalid then col = m.chPal.cyan
    ch = s.chart
    has = (ch <> invalid and ch.series <> invalid and ch.series.count() > 1)
    pts = s.points
    if pts = invalid then pts = []
    m.chSegs = []
    m.chHead = invalid
    if has
        p = cpanel(60, 290, 1100, 420, "", false)
        for k = 1 to 3
            rect(p, 0, k * 105, 1100, 1, m.C.line)
        end for
        if ch.lines <> invalid
            for each ln in ch.lines
                y = 400 - Int(ln.y * 380)
                for xx = 0 to 1070 step 30
                    rect(p, 10 + xx, y, 16, 2, m.C.amber)
                end for
                lbl(p, txt(ln.label), 640, y - 28, 450, 20, true, m.C.amber, "right")
            end for
        end if
        sr = ch.series
        if ch.dir <> invalid and ch.dir < 0 then lc = m.C.red else lc = m.C.green
        n = sr.count()
        for k = 0 to n - 2
            if sr[k] >= 0 and sr[k + 1] >= 0
                xa = 20 + Int(k * 1060 / (n - 1))
                xb = 20 + Int((k + 1) * 1060 / (n - 1))
                ya = 400 - Int(sr[k] * 380)
                yb = 400 - Int(sr[k + 1] * 380)
                top = ya
                if yb < top then top = yb
                ln2 = rect(p, xa, top - 2, xb - xa + 2, Abs(ya - yb) + 4, lc)
                ln2.visible = false
                m.chSegs.push({ f: invalid, l: ln2, x: xb, y: yb })
            end if
        end for
        m.chHead = rect(p, 0, 0, 14, 14, m.C.white)
        m.chHead.visible = false
        labs = ch.labels
        if labs = invalid then labs = []
        for k = 0 to labs.count() - 1
            x = 80 + Int(k * 1040 / 4)
            if k = labs.count() - 1 then x = x - 40
            lbl(m.chBody, txt(labs[k]), x, 716, 200, 20, false, m.C.dim)
        end for
        for i = 0 to pts.count() - 1
            if i < 2
                g = m.chBody.createChild("Group")
                lbl(g, "• " + txt(pts[i]), 0, 0, 1100, 26, true, m.C.white)
                chAnim(g, 60, 748 + i * 38, 0.9 + i * 0.5, "pop")
            end if
        end for
    else
        for i = 0 to pts.count() - 1
            y = 290 + i * 128
            g = cpanel(60, y, 1100, 112, col, false)
            l = lbl(g, txt(pts[i]), 28, 12, 1040, 32, true, m.C.white)
            l.wrap = true
            l.maxLines = 2
            chAnim(g, 60, y, 0.3 + i * 0.7, "right")
        end for
    end if
    if txt(s.name) <> ""
        g = m.chBody.createChild("Group")
        lbl(g, txt(s.name), 0, 0, 660, 26, true, col)
        chAnim(g, 1200, 300, 0.1, "pop")
    end if
    if txt(s.big) <> ""
        g = m.chBody.createChild("Group")
        if Len(txt(s.big)) > 9 then sz = 56 else sz = 92
        lbl(g, txt(s.big), 0, 0, 660, sz, true, dirColor(s.bigdir))
        chAnim(g, 1200, 330, 0.3, "pop")
    end if
    chips = s.chips
    if chips = invalid then chips = []
    for i = 0 to chips.count() - 1
        c = chips[i]
        x = 1200 + (i mod 2) * 335
        y = 460 + Int(i / 2) * 92
        g = m.chBody.createChild("Group")
        rect(g, 0, 0, 320, 82, m.C.panel)
        if c.dir <> invalid and c.dir > 0
            ec = m.C.green
        else if c.dir <> invalid and c.dir < 0
            ec = m.C.red
        else
            ec = m.C.line
        end if
        rect(g, 0, 0, 320, 4, ec)
        lbl(g, txt(c.k), 18, 12, 290, 18, true, m.C.dim)
        lbl(g, txt(c.v), 18, 38, 290, 32, true, dirColor(c.dir))
        chAnim(g, x, y, 0.6 + i * 0.22, "pop")
    end for
end sub

sub chDiveFrame(p as float)
    n = m.chSegs.count()
    if n = 0 or m.chHead = invalid then return
    k = ease(p / 0.5)
    upto = Int(k * n)
    for i = 0 to n - 1
        m.chSegs[i].l.visible = (i < upto)
    end for
    if upto > 0
        h = m.chSegs[upto - 1]
        m.chHead.translation = [h.x - 6, h.y - 6]
        m.chHead.visible = true
    end if
end sub
