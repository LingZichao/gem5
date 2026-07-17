current_design uacc_collector_uacc_collector

set clk_name core_clock
set clk_port [get_ports ap_clk]
# Use the same 5 ns controller clock as the allocator.
set clk_period 5000
set io_delay [expr {$clk_period * 0.2}]

create_clock -name $clk_name -period $clk_period $clk_port
set_input_delay $io_delay -clock $clk_name [all_inputs -no_clocks]
set_output_delay $io_delay -clock $clk_name [all_outputs]
set_false_path -from [get_ports ap_rst]
