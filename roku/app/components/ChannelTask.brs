' Alex's channel: fetches the show's rundown and the anchor's sound files from the board. GET only.
' want = "rundown"  -> rundown (the list of segments)
' want = "clip:<f>" -> clip { name, path } once that sound file is saved on the Roku (tmp:/)
sub init()
    m.top.functionName = "serve"
end sub

sub serve()
    cfg = BoardConfig()
    reg = CreateObject("roRegistrySection", "board")
    home = cfg.home
    if reg.Read("home") <> "" and reg.Read("built") = cfg.home then home = reg.Read("home")
    m.routes = [chan(home)]
    if Left(cfg.away, 8) = "https://" then m.routes.push(chan(cfg.away))
    m.best = 0
    m.saved = []
    port = CreateObject("roMessagePort")
    m.top.observeField("want", port)
    queue = []
    while true
        if queue.count() = 0
            msg = wait(0, port)
            if type(msg) = "roSGNodeEvent" then queue.push(msg.getData())
        end if
        ' take everything waiting, newest rundown request first
        while true
            msg = port.GetMessage()
            if msg = invalid then exit while
            if type(msg) = "roSGNodeEvent" then queue.push(msg.getData())
        end while
        w = queue.shift()
        if w = "rundown"
            got = getText("")
            if got.data <> invalid
                m.top.rundown = got.data
                m.top.problem = ""
            else
                m.top.problem = got.problem
            end if
        else if Left(w, 5) = "clip:"
            name = Mid(w, 6)
            path = "tmp:/ch-" + name
            ok = getFile("&f=" + name, path)
            if ok
                m.saved.push(path)
                ' keep only the newest sound files on the Roku
                fs = CreateObject("roFileSystem")
                while m.saved.count() > 24
                    fs.Delete(m.saved.shift())
                end while
            end if
            m.top.clip = { name: name, path: path, ok: ok }
        end if
    end while
end sub

function chan(u as string) as string
    return u.Replace("/api/tv?", "/api/tvchannel?")
end function

function request(url as string) as object
    http = CreateObject("roUrlTransfer")
    http.SetMessagePort(CreateObject("roMessagePort"))
    http.SetUrl(url)
    if Left(url, 8) = "https://"
        http.SetCertificatesFile("common:/certs/ca-bundle.crt")
        http.InitClientCertificates()
    end if
    http.RetainBodyOnError(true)
    return http
end function

function getText(extra as string) as object
    problem = ""
    for n = 0 to m.routes.count() - 1
        i = (m.best + n) mod m.routes.count()
        http = request(m.routes[i] + extra)
        if http.AsyncGetToString()
            msg = wait(15000, http.GetMessagePort())
            if type(msg) = "roUrlEvent"
                data = ParseJson(msg.GetString())
                if msg.GetResponseCode() = 200 and data <> invalid
                    m.best = i
                    if data.ok = true then return { data: data, problem: "" }
                    if data.error <> invalid then return { data: invalid, problem: data.error }
                end if
                problem = "The board answered with an error (" + msg.GetResponseCode().ToStr() + ")"
            else
                http.AsyncCancel()
                problem = "Can't reach the Mac right now"
            end if
        end if
    end for
    return { data: invalid, problem: problem }
end function

function getFile(extra as string, path as string) as boolean
    for n = 0 to m.routes.count() - 1
        i = (m.best + n) mod m.routes.count()
        http = request(m.routes[i] + extra)
        if http.AsyncGetToFile(path)
            msg = wait(20000, http.GetMessagePort())
            if type(msg) = "roUrlEvent" and msg.GetResponseCode() = 200
                m.best = i
                return true
            end if
            if type(msg) <> "roUrlEvent" then http.AsyncCancel()
        end if
    end for
    return false
end function
