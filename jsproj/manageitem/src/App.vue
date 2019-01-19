<template>
  <div id="app">
    <ItemManagement/>
  </div>
</template>

<script>
import ItemManagement from './components/ItemManagement'

export default {
  name: 'app',
  components: {
    ItemManagement
  }
}
</script>

<style>
#app {
  font-family: 'Avenir', Helvetica, Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  text-align: center;
  color: #2c3e50;
  margin-top: 60px;
}
</style>
