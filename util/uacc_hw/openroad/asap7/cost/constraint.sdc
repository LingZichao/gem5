current_design uacc_section14_cost_uacc_section14_cost

set clk_name core_clock
set clk_port [get_ports ap_clk]
# ASAP7 liberty and SDC time units are picoseconds: 2000 ps = 2 ns.
set clk_period 2000
set io_delay [expr {$clk_period * 0.2}]

create_clock -name $clk_name -period $clk_period $clk_port
set_input_delay $io_delay -clock $clk_name [all_inputs -no_clocks]
set_output_delay $io_delay -clock $clk_name [all_outputs]
set_false_path -from [get_ports ap_rst]
