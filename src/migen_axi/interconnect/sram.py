from operator import attrgetter
from migen import *
from . import axi

__all__ = ["SRAM"]


class SRAM(Module):
    def __init__(self, mem_or_size, read_only=False, init=None, bus=None):

        # SRAM initialisation

        if bus is None:
            bus = axi.Interface()
        self.bus = bus
        bus_data_width = len(self.bus.r.data)
        if isinstance(mem_or_size, Memory):
            assert(mem_or_size.width <= bus_data_width)
            self.mem = mem_or_size
        else:
            self.mem = Memory(bus_data_width, mem_or_size//(bus_data_width//8), init=init)

        # memory
        port = self.mem.get_port(write_capable=not read_only, we_granularity=8)
        self.port = port
        self.specials += self.mem, port

        ###

        ar, aw, w, r, b = attrgetter("ar", "aw", "w", "r", "b")(bus)

        id_ = Signal(len(ar.id), reset_less=True)

        dout_index = Signal.like(ar.len)

        self.r_addr_incr = axi.Incr(ar)
        self.w_addr_incr = axi.Incr(aw)

        ### Read

        self.sync += [
            r.data.eq(port.dat_r),
            port.adr.eq(self.r_addr_incr.addr)
        ]

        self.comb += [
            r.id.eq(id_),
            b.id.eq(id_),
            r.resp.eq(axi.Response.okay),
            b.resp.eq(axi.Response.okay),
        ]

        # read control
        self.submodules.read_fsm = read_fsm = FSM(reset_state="IDLE")
        read_fsm.act("IDLE",
            If(ar.valid,
                ar.ready.eq(1),
                NextValue(id_, ar.id),
                NextState("READ_START"),
            )
        )
        read_fsm.act("READ_START",
            r.resp.eq(axi.Response.okay),
            r.valid.eq(1),
            If(r.ready,
                NextState("READ"))
        )
        read_fsm.act("READ",
            If(r.last & r.ready, # that's a smart way of skipping "LAST" state    
                NextState("IDLE")
            )
        )

        self.sync += [
            If(read_fsm.ongoing("IDLE"),
                dout_index.eq(0),
                r.valid.eq(0),  # shall it be reset too on IDLE?
                ar.ready.eq(0),
                r.last.eq(0)
            ).Else(If(r.ready & read_fsm.ongoing("READ"),
                    dout_index.eq(dout_index+1),
                    If(dout_index==ar.len, r.last.eq(1)) # and update last
                )
            )
        ]

        ### Write

        if not read_only:
            self.comb += [
                port.dat_w.eq(w.data),
                port.adr.eq(self.w_addr_incr.addr),
            ]

            self.submodules.write_fsm = write_fsm = FSM(reset_state="IDLE")
            write_fsm.act("IDLE",
                w.ready.eq(0),
                aw.ready.eq(0),
                If(aw.valid,
                    NextValue(aw.ready, 1),
                    NextValue(id_, aw.id),
                    NextState("AW_VALID_WAIT")
                )
            )
            write_fsm.act("AW_VALID_WAIT",  # wait for data
                aw.ready.eq(1),
                If(w.valid,
                    NextState("WRITE"),
                )
            )
            # write_fsm.act("DATA_WAIT",
            #     aw.valid.eq(0),
            #     If(self.din_ready,
            #         w.valid.eq(1),
            #         NextState("WRITE")
            #     )
            # )
            write_fsm.act("WRITE",
                w.ready.eq(1),
                If(w.ready & w.last,
                    NextState("WRITE_RESP")
                )
            )

            write_fsm.act("WRITE_RESP",
                port.we.eq(0),
                b.resp.eq(axi.Response.okay),
                b.valid.eq(1),
                If(b.ready,
                    NextState("IDLE")
                )
            )

            self.sync += If(w.ready & w.valid, port.we.eq(0xF))
