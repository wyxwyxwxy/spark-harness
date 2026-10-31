package com.sparkharness.app

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.sparkharness.app.util.Prefs

/** 开机自启：仅当用户在“设置”中开启“开机自动连接”时，启动前台连接服务。 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED && Prefs.autostart) {
            ConnectionService.start(context)
        }
    }
}
