plugins {
    id("com.android.application")
    id("com.chaquo.python")
}

// Use only the MIT-licensed native engine from the pinned Maven Central AAR.
// Our tiny Java JNI adapter avoids pulling its Kotlin/UI/model stack into Reticom.
val valhallaNative by configurations.creating { isTransitive = false }
val unpackValhalla by tasks.registering(Sync::class) {
    from({ zipTree(valhallaNative.singleFile) }) { include("jni/arm64-v8a/*.so") }
    into(layout.buildDirectory.dir("generated/valhalla"))
}
tasks.named("preBuild") { dependsOn(unpackValhalla) }

val reticomTransportHost = providers.gradleProperty("reticomTransportHost").orNull?.trim().orEmpty()
val reticomTransportPort = providers.gradleProperty("reticomTransportPort").orNull?.toIntOrNull() ?: 4242
val reticomVersionName = providers.gradleProperty("reticomVersionName").orNull?.trim().orEmpty().ifBlank { "0.1.0" }
val reticomVersionCode = providers.gradleProperty("reticomVersionCode").orNull?.toIntOrNull() ?: 1

require(reticomVersionName.matches(Regex("^[0-9]+\\.[0-9]+\\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$"))) {
    "reticomVersionName must be a semantic version without a leading v"
}
require(reticomVersionCode in 1..2_100_000_000) {
    "reticomVersionCode must be between 1 and 2100000000"
}

android {
    namespace = "com.retium.field"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.retium.field"
        minSdk = 26
        targetSdk = 35
        versionCode = reticomVersionCode
        versionName = reticomVersionName
        buildConfigField("String", "RETICOM_TRANSPORT_HOST", "\"${reticomTransportHost.replace("\\", "\\\\").replace("\"", "\\\"")}\"")
        buildConfigField("int", "RETICOM_TRANSPORT_PORT", reticomTransportPort.toString())

        ndk {
            abiFilters += listOf("arm64-v8a")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        buildConfig = true
    }
    sourceSets.getByName("main").jniLibs.srcDir(layout.buildDirectory.dir("generated/valhalla/jni"))
}

chaquopy {
    defaultConfig {
        version = "3.11"
        System.getenv("RETIUM_BUILD_PYTHON")?.let { buildPython(it) }
        pip {
            install("rns==1.5.2")
            install("lxmf==1.1.1")
            install("cryptography==42.0.8")
            install("pyserial==3.5")
            install("fastapi==0.99.1")
            install("pydantic==1.10.13")
            install("uvicorn==0.23.2")
            install("wsproto==1.2.0")
            install("websockets==15.0.1")
            install("qrcode==8.2")
            // Latest Chaquopy cp311 Android wheel (arm64-v8a and x86_64).
            // Desktop's Pillow pin has no cp311 Android wheel in this index.
            // https://chaquo.com/pypi-13.1/pillow/
            install("Pillow==11.0.0")
        }
        pyc {
            src = true
            pip = true
        }
        extractPackages("retium")
    }
    sourceSets {
        getByName("main") {
            srcDir("../../src")
            srcDir("src/main/python")
        }
    }
}

dependencies {
    valhallaNative("io.github.rallista:valhalla-mobile:0.6.3@aar")
    implementation("com.google.android.gms:play-services-code-scanner:16.1.0")
}
