import { ref, getCurrentInstance } from "vue";
import {
    initPromise,
    checkDbHealth,
    getOpeningStorage,
    setOpeningStorage,
    clearOpeningStorage,
    setTaxTemplate,
} from "../../offline/index.js";

export function usePosShift(openDialog) {
    const { proxy } = getCurrentInstance();
    const eventBus = proxy?.eventBus;

    const pos_profile = ref(null);
    const pos_opening_shift = ref(null);

    async function check_opening_entry() {
        await initPromise;
        await checkDbHealth();
        return frappe
            .call("posawesome.posawesome.api.shifts.check_opening_shift", {
                user: frappe.session.user,
            })
            .then((r) => {
                if (r.message) {
                    pos_profile.value = r.message.pos_profile;
                    pos_opening_shift.value = r.message.pos_opening_shift;
                    if (pos_profile.value.taxes_and_charges) {
                        frappe.call({
                            method: "frappe.client.get",
                            args: {
                                doctype: "Sales Taxes and Charges Template",
                                name: pos_profile.value.taxes_and_charges,
                            },
                            callback: (res) => {
                                if (res.message) {
                                    setTaxTemplate(
                                        pos_profile.value.taxes_and_charges,
                                        res.message,
                                    );
                                }
                            },
                        });
                    }
                    eventBus?.emit("register_pos_profile", r.message);
                    eventBus?.emit("set_company", r.message.company);
                    try {
                        frappe.realtime.emit("pos_profile_registered");
                    } catch (e) {
                        console.warn("Realtime emit failed", e);
                    }
                    console.info("LoadPosProfile");
                    try {
                        setOpeningStorage(r.message);
                    } catch (e) {
                        console.error("Failed to cache opening data", e);
                    }
                } else {
                    const data = getOpeningStorage();
                    if (data) {
                        pos_profile.value = data.pos_profile;
                        pos_opening_shift.value = data.pos_opening_shift;
                        eventBus?.emit("register_pos_profile", data);
                        eventBus?.emit("set_company", data.company);
                        try {
                            frappe.realtime.emit("pos_profile_registered");
                        } catch (e) {
                            console.warn("Realtime emit failed", e);
                        }
                        console.info("LoadPosProfile (cached)");
                        return;
                    }
                    openDialog && openDialog();
                }
            })
            .catch(() => {
                const data = getOpeningStorage();
                if (data) {
                    pos_profile.value = data.pos_profile;
                    pos_opening_shift.value = data.pos_opening_shift;
                    eventBus?.emit("register_pos_profile", data);
                    eventBus?.emit("set_company", data.company);
                    try {
                        frappe.realtime.emit("pos_profile_registered");
                    } catch (e) {
                        console.warn("Realtime emit failed", e);
                    }
                    console.info("LoadPosProfile (cached)");
                    return;
                }
                openDialog && openDialog();
            });
    }

    function get_closing_data() {
        return frappe
            .call(
                "posawesome.posawesome.doctype.pos_closing_shift.pos_closing_shift.make_closing_shift_from_opening",
                { opening_shift: pos_opening_shift.value },
            )
            .then((r) => {
                if (r.message) {
                    eventBus?.emit("open_ClosingDialog", r.message);
                }
            });
    }

    function submit_closing_pos(data) {
        frappe
            .call(
                "posawesome.posawesome.doctype.pos_closing_shift.pos_closing_shift.submit_closing_shift",
                { closing_shift: data },
            )
            .then((r) => {
                if (r.message) {
                    pos_opening_shift.value = null;
                    pos_profile.value = null;
                    clearOpeningStorage();
                    eventBus?.emit("show_message", {
                        title: `POS Shift Closed`,
                        color: "success",
                    });
                    
                    // Automatically print today's report after closing shift
                    print_todays_report();
                    
                    check_opening_entry();
                }
            });
    }

    async function print_todays_report() {
        try {
            // Get the HTML report content
            const response = await frappe.call({
                method: "posawesome.posawesome.doctype.pos_closing_shift.pos_closing_shift.get_todays_shifts_report",
            });

            if (response.message) {
                // Create a new window with the report content
                const printWindow = window.open("", "_blank");
                printWindow.document.write(response.message);
                printWindow.document.close();
                printWindow.focus();
                
                // Wait for the content to load then print
                printWindow.addEventListener("load", () => {
                    printWindow.print();
                }, { once: true });
            }
        } catch (error) {
            console.error("Error printing today's report:", error);
            eventBus?.emit("show_message", {
                title: `Error printing report`,
                color: "error",
            });
        }
    }

    return { pos_profile, pos_opening_shift, check_opening_entry, get_closing_data, submit_closing_pos };
}
