' Asks the board for the IBKR tiles every 2 seconds and the rest every 15 seconds. GET only.
' Tries the way that worked last time first (home Wi-Fi, else the ts.net link), then the other.
sub init()
    m.top.functionName = "pollBoard"
end sub

sub pollBoard()
    cfg = BoardConfig()
    ' the Mac's newest home address, learned from the board itself (see below), beats the one built in
    m.reg = CreateObject("roRegistrySection", "board")
    home = cfg.home
    saved = m.reg.Read("home")
    if saved <> "" and m.reg.Read("built") = cfg.home then home = saved
    m.builtHome = cfg.home
    m.routes = [{ name: "home Wi-Fi", url: home }]
    if Left(cfg.away, 8) = "https://" then m.routes.push({ name: "ts.net", url: cfg.away })
    m.best = 0
    tick = 0
    port = CreateObject("roMessagePort")
    m.top.observeField("retry", port)
    while true
        if tick mod 8 = 0
            ans = ask("")
            if ans.data <> invalid
                m.top.result = ans.data
                m.top.problem = ""
                learnHome(ans.data.home)
            else
                m.top.problem = ans.problem
            end if
        end if
        ans = ask("&part=tiles")
        if ans.data <> invalid
            m.top.tiles = ans.data
        else
            m.top.problem = ans.problem
        end if
        tick = tick + 1
        ' wait 2 seconds, or less if OK was pressed on the remote (then ask for everything again now)
        msg = wait(2000, port)
        if msg <> invalid then tick = 0
    end while
end sub

' The board tells the TV its current home Wi-Fi address. If the Mac's address changed (the router
' gave it a new one), the TV keeps using the new one from then on, so nothing needs remaking.
sub learnHome(home as dynamic)
    if home = invalid or Left(home, 7) <> "http://" then return
    if home = m.routes[0].url then return
    m.routes[0].url = home
    m.reg.Write("home", home)
    m.reg.Write("built", m.builtHome)
    m.reg.Flush()
end sub

' one request, trying each way to the Mac (the one that worked last time first)
function ask(extra as string) as object
    problem = ""
    for n = 0 to m.routes.count() - 1
        i = (m.best + n) mod m.routes.count()
        ans = fetch(m.routes[i].url + extra)
        if ans.data <> invalid
            m.best = i
            m.top.route = m.routes[i].name
            return ans
        end if
        if problem = "" or ans.reached then problem = ans.problem
    end for
    return { data: invalid, problem: problem }
end function

' -> { data: the answer or invalid, problem: why not, reached: true if the board answered }
function fetch(url as string) as object
    http = CreateObject("roUrlTransfer")
    port = CreateObject("roMessagePort")
    http.SetMessagePort(port)
    http.SetUrl(url)
    if Left(url, 8) = "https://"
        http.SetCertificatesFile("common:/certs/ca-bundle.crt")
        http.InitClientCertificates()
    end if
    http.RetainBodyOnError(true)
    http.EnableEncodings(true)
    if not http.AsyncGetToString() then return { data: invalid, problem: "Could not start a request", reached: false }
    msg = wait(8000, port)
    if type(msg) <> "roUrlEvent"
        http.AsyncCancel()
        return { data: invalid, problem: "The Mac took too long to answer", reached: false }
    end if
    code = msg.GetResponseCode()
    data = ParseJson(msg.GetString())
    if code = 200 and data <> invalid and data.ok = true then return { data: data, problem: "", reached: true }
    if data <> invalid and data.error <> invalid then return { data: invalid, problem: data.error, reached: true }
    if code < 0 then return { data: invalid, problem: "Can't reach the Mac. Is it on and online?", reached: false }
    return { data: invalid, problem: "The board answered with an error (" + code.ToStr() + ")", reached: true }
end function
