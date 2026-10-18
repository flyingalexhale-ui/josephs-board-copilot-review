' Alex Hale's Board, Roku TV view (jb-roku). READ ONLY: this app only reads a summary
' from the board on the Mac (GET /api/tv). It has no way to trade or change anything.
sub Main()
    screen = CreateObject("roSGScreen")
    port = CreateObject("roMessagePort")
    screen.setMessagePort(port)
    screen.CreateScene("BoardScene")
    screen.show()
    while true
        msg = wait(0, port)
        if type(msg) = "roSGScreenEvent" and msg.isScreenClosed() then return
    end while
end sub
